"""Stage 1 — ingest the per-study JSON zip into DuckDB raw tables (spec §4.1, §5 Stage 1).

Single pass: zip member → json.loads → pruned Pydantic boundary model (extra="ignore"; resultsSection /
documentSection / locations never touched) → per-table row batches → DuckDB via Arrow. No intermediate
NDJSON. Work is split across processes by zip-member chunks; each worker re-opens the zip.

Census (printed + written to build_meta): n_read, n_loaded, parse failures LISTED, per-module absence
counts, arm-join path counts. snapshot_date = max(last_update_date_parsed) FROM THE DATA.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import zipfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field

from ct_landscape.db import RAW_TABLES, create_raw_schema, write_meta
from ct_landscape.normalize.phases import phase_norm

# ---------------------------------------------------------------- boundary models (lean, extra ignored)


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Organization(_M):
    fullName: str | None = None
    class_: str | None = Field(default=None, alias="class")


class IdentificationModule(_M):
    nctId: str
    briefTitle: str | None = None
    officialTitle: str | None = None
    organization: Organization | None = None


class DateStruct(_M):
    date: str | None = None
    type: str | None = None


class StatusModule(_M):
    overallStatus: str | None = None
    startDateStruct: DateStruct | None = None
    primaryCompletionDateStruct: DateStruct | None = None
    completionDateStruct: DateStruct | None = None
    lastUpdatePostDateStruct: DateStruct | None = None
    studyFirstSubmitDate: str | None = None


class Agency(_M):
    name: str | None = None
    class_: str | None = Field(default=None, alias="class")


class SponsorCollaboratorsModule(_M):
    leadSponsor: Agency | None = None
    collaborators: list[Agency] = []


class DescriptionModule(_M):
    briefSummary: str | None = None


class ConditionsModule(_M):
    conditions: list[str] = []
    keywords: list[str] = []


class EnrollmentInfo(_M):
    count: int | None = None
    type: str | None = None


class DesignInfo(_M):
    primaryPurpose: str | None = None


class DesignModule(_M):
    studyType: str | None = None
    phases: list[str] = []
    enrollmentInfo: EnrollmentInfo | None = None
    designInfo: DesignInfo | None = None


class ArmGroup(_M):
    label: str | None = None
    type: str | None = None
    description: str | None = None
    interventionNames: list[str] = []


class Intervention(_M):
    type: str | None = None
    name: str | None = None
    description: str | None = None
    armGroupLabels: list[str] = []
    otherNames: list[str] = []


class ArmsInterventionsModule(_M):
    armGroups: list[ArmGroup] = []
    interventions: list[Intervention] = []


class EligibilityModule(_M):
    eligibilityCriteria: str | None = None
    healthyVolunteers: bool | None = None
    sex: str | None = None
    minimumAge: str | None = None
    maximumAge: str | None = None
    stdAges: list[str] = []


class ProtocolSection(_M):
    identificationModule: IdentificationModule
    statusModule: StatusModule | None = None
    sponsorCollaboratorsModule: SponsorCollaboratorsModule | None = None
    descriptionModule: DescriptionModule | None = None
    conditionsModule: ConditionsModule | None = None
    designModule: DesignModule | None = None
    armsInterventionsModule: ArmsInterventionsModule | None = None
    eligibilityModule: EligibilityModule | None = None


class MeshEntry(_M):
    id: str | None = None
    term: str | None = None


class BrowseModule(_M):
    meshes: list[MeshEntry] = []
    ancestors: list[MeshEntry] = []


class DerivedSection(_M):
    conditionBrowseModule: BrowseModule | None = None
    interventionBrowseModule: BrowseModule | None = None


class Study(_M):
    protocolSection: ProtocolSection
    derivedSection: DerivedSection | None = None
    hasResults: bool | None = None


# ---------------------------------------------------------------- pure helpers

_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")
_PRECISION_ORDER = {"day": 0, "month": 1, "year": 2}
_INTERVENTION_NAME_RE = re.compile(r"^\s*([A-Za-z_ ]+?)\s*:\s*(.+?)\s*$")


def parse_partial_date(s: str | None) -> tuple[date | None, str | None]:
    """'YYYY-MM-DD' | 'YYYY-MM' | 'YYYY' → (date padded to period start, precision). Bad → (None, None)."""
    if not s:
        return None, None
    m = _DATE_RE.match(s.strip())
    if not m:
        return None, None
    y, mo, d = m.groups()
    try:
        if d:
            return date(int(y), int(mo), int(d)), "day"
        if mo:
            return date(int(y), int(mo), 1), "month"
        return date(int(y), 1, 1), "year"
    except ValueError:
        return None, None


def _coarsest(precisions: list[str | None]) -> str | None:
    present = [p for p in precisions if p]
    return max(present, key=_PRECISION_ORDER.__getitem__) if present else None


def _split_intervention_name(s: str) -> tuple[str | None, str]:
    """'Drug: Pembrolizumab' → ('DRUG', 'Pembrolizumab'); untyped → (None, s)."""
    m = _INTERVENTION_NAME_RE.match(s)
    if not m:
        return None, s.strip()
    return m.group(1).strip().upper().replace(" ", "_"), m.group(2)


@dataclass
class Batch:
    rows: dict[str, list[tuple]] = field(default_factory=lambda: {t: [] for t in RAW_TABLES})
    census: Counter = field(default_factory=Counter)

    def extend(self, other: Batch) -> None:
        for t, r in other.rows.items():
            self.rows[t].extend(r)
        self.census.update(other.census)


def parse_study(raw: dict[str, Any], out: Batch) -> None:
    """Append one study's rows to `out`. Raises on schema violation (caller records the failure)."""
    st = Study.model_validate(raw)
    ps = st.protocolSection
    c = out.census
    nct = ps.identificationModule.nctId

    status = ps.statusModule or StatusModule()
    design = ps.designModule or DesignModule()
    elig = ps.eligibilityModule or EligibilityModule()
    org = ps.identificationModule.organization

    dates = {
        "start": (status.startDateStruct or DateStruct()).date,
        "primary_completion": (status.primaryCompletionDateStruct or DateStruct()).date,
        "completion": (status.completionDateStruct or DateStruct()).date,
        "last_update": (status.lastUpdatePostDateStruct or DateStruct()).date,
    }
    parsed = {k: parse_partial_date(v) for k, v in dates.items()}
    enroll = design.enrollmentInfo or EnrollmentInfo()

    if not design.phases:
        c["n_no_phases"] += 1
    if not ps.armsInterventionsModule or not ps.armsInterventionsModule.armGroups:
        c["n_no_arms"] += 1
    if not ps.armsInterventionsModule or not ps.armsInterventionsModule.interventions:
        c["n_no_interventions"] += 1
    if not ps.conditionsModule or not ps.conditionsModule.conditions:
        c["n_no_conditions"] += 1
    if not elig.eligibilityCriteria:
        c["n_no_eligibility_text"] += 1
    if not ps.sponsorCollaboratorsModule or not ps.sponsorCollaboratorsModule.leadSponsor:
        c["n_no_lead_sponsor"] += 1
    if enroll.count is None:
        c["n_no_enrollment"] += 1
    if not dates["last_update"]:
        c["n_no_last_update_date"] += 1

    out.rows["studies"].append(
        (
            nct,
            ps.identificationModule.briefTitle,
            ps.identificationModule.officialTitle,
            org.fullName if org else None,
            org.class_ if org else None,
            status.overallStatus,
            design.studyType,
            phase_norm(design.phases),
            enroll.count,
            enroll.type,
            dates["start"],
            dates["primary_completion"],
            dates["completion"],
            dates["last_update"],
            parsed["start"][0],
            parsed["primary_completion"][0],
            parsed["completion"][0],
            parsed["last_update"][0],
            _coarsest([p[1] for p in parsed.values()]),
            parse_partial_date(status.studyFirstSubmitDate)[0],
            (ps.descriptionModule or DescriptionModule()).briefSummary,
            elig.eligibilityCriteria,
            elig.healthyVolunteers,
            elig.sex,
            elig.minimumAge,
            elig.maximumAge,
            list(elig.stdAges),
            (design.designInfo or DesignInfo()).primaryPurpose,
            st.hasResults,
        )
    )

    cm = ps.conditionsModule or ConditionsModule()
    out.rows["study_conditions"].extend((nct, i, s) for i, s in enumerate(cm.conditions))
    out.rows["study_keywords"].extend((nct, i, s) for i, s in enumerate(cm.keywords))

    sc = ps.sponsorCollaboratorsModule or SponsorCollaboratorsModule()
    if sc.leadSponsor:
        out.rows["sponsors"].append((nct, "lead", sc.leadSponsor.name, sc.leadSponsor.class_))
    out.rows["sponsors"].extend((nct, "collaborator", a.name, a.class_) for a in sc.collaborators)

    ai = ps.armsInterventionsModule or ArmsInterventionsModule()
    for j, iv in enumerate(ai.interventions):
        out.rows["interventions"].append((nct, j, iv.type, iv.name, iv.description))
        out.rows["intervention_other_names"].extend((nct, j, o) for o in iv.otherNames)
    label_to_arm: dict[str, int] = {}
    for k, arm in enumerate(ai.armGroups):
        out.rows["arms"].append((nct, k, arm.label, arm.type, arm.description))
        if arm.label is not None:
            label_to_arm.setdefault(arm.label, k)

    # arm ↔ intervention join: armGroupLabels exact-label match first; interventionNames fallback
    linked: set[tuple[int, int]] = set()
    for j, iv in enumerate(ai.interventions):
        for lbl in iv.armGroupLabels:
            k = label_to_arm.get(lbl)
            if k is None:
                c["n_arm_label_unmatched"] += 1
                continue
            if (k, j) not in linked:
                linked.add((k, j))
                out.rows["arm_interventions"].append((nct, k, j, "label"))
                c["n_arm_links_via_label"] += 1
    if ai.interventions and ai.armGroups and not any(iv.armGroupLabels for iv in ai.interventions):
        by_type_name = {
            ((iv.type or "").upper(), (iv.name or "").strip().lower()): j
            for j, iv in enumerate(ai.interventions)
        }
        by_name = {(iv.name or "").strip().lower(): j for j, iv in enumerate(ai.interventions)}
        for k, arm in enumerate(ai.armGroups):
            for s in arm.interventionNames:
                typ, name = _split_intervention_name(s)
                j = by_type_name.get((typ or "", name.lower())) if typ else None
                if j is None:
                    j = by_name.get(name.lower())
                if j is None:
                    c["n_arm_name_unmatched"] += 1
                    continue
                if (k, j) not in linked:
                    linked.add((k, j))
                    out.rows["arm_interventions"].append((nct, k, j, "name"))
                    c["n_arm_links_via_name"] += 1

    ds = st.derivedSection or DerivedSection()
    for module, bm in (
        ("condition", ds.conditionBrowseModule),
        ("intervention", ds.interventionBrowseModule),
    ):
        if not bm or not (bm.meshes or bm.ancestors):
            c[f"n_no_derived_mesh_{module}"] += 1
            continue
        out.rows["mesh_terms"].extend((nct, module, "mesh", m.id, m.term) for m in bm.meshes)
        out.rows["mesh_terms"].extend((nct, module, "ancestor", m.id, m.term) for m in bm.ancestors)

    c["n_loaded"] += 1


# ---------------------------------------------------------------- workers / loading

ARROW_SCHEMAS: dict[str, pa.Schema] = {
    "studies": pa.schema(
        [
            ("nct_id", pa.string()),
            ("brief_title", pa.string()),
            ("official_title", pa.string()),
            ("org_name", pa.string()),
            ("org_class", pa.string()),
            ("overall_status", pa.string()),
            ("study_type", pa.string()),
            ("phase_norm", pa.string()),
            ("enrollment_count", pa.int64()),
            ("enrollment_type", pa.string()),
            ("start_date", pa.string()),
            ("primary_completion_date", pa.string()),
            ("completion_date", pa.string()),
            ("last_update_date", pa.string()),
            ("start_date_parsed", pa.date32()),
            ("primary_completion_date_parsed", pa.date32()),
            ("completion_date_parsed", pa.date32()),
            ("last_update_date_parsed", pa.date32()),
            ("date_precision", pa.string()),
            ("study_first_submit_date", pa.date32()),
            ("brief_summary", pa.string()),
            ("eligibility_criteria", pa.string()),
            ("healthy_volunteers", pa.bool_()),
            ("sex", pa.string()),
            ("minimum_age", pa.string()),
            ("maximum_age", pa.string()),
            ("std_ages", pa.list_(pa.string())),
            ("primary_purpose", pa.string()),
            ("has_results", pa.bool_()),
        ]
    ),
    "study_conditions": pa.schema(
        [("nct_id", pa.string()), ("position", pa.int32()), ("name_raw", pa.string())]
    ),
    "study_keywords": pa.schema(
        [("nct_id", pa.string()), ("position", pa.int32()), ("keyword_raw", pa.string())]
    ),
    "interventions": pa.schema(
        [
            ("nct_id", pa.string()),
            ("intervention_no", pa.int32()),
            ("type", pa.string()),
            ("name_raw", pa.string()),
            ("description", pa.string()),
        ]
    ),
    "intervention_other_names": pa.schema(
        [("nct_id", pa.string()), ("intervention_no", pa.int32()), ("other_name_raw", pa.string())]
    ),
    "arms": pa.schema(
        [
            ("nct_id", pa.string()),
            ("arm_no", pa.int32()),
            ("label", pa.string()),
            ("type", pa.string()),
            ("description", pa.string()),
        ]
    ),
    "arm_interventions": pa.schema(
        [
            ("nct_id", pa.string()),
            ("arm_no", pa.int32()),
            ("intervention_no", pa.int32()),
            ("via", pa.string()),
        ]
    ),
    "sponsors": pa.schema(
        [
            ("nct_id", pa.string()),
            ("role", pa.string()),
            ("name_raw", pa.string()),
            ("agency_class", pa.string()),
        ]
    ),
    "mesh_terms": pa.schema(
        [
            ("nct_id", pa.string()),
            ("module", pa.string()),
            ("kind", pa.string()),
            ("mesh_id", pa.string()),
            ("term", pa.string()),
        ]
    ),
    "ingest_failures": pa.schema([("member", pa.string()), ("error", pa.string())]),
}


def _parse_members(zip_path: str, members: list[str]) -> Batch:
    """Worker: parse a chunk of zip members into row batches. Failures are rows, not exceptions."""
    out = Batch()
    with zipfile.ZipFile(zip_path) as zf:
        for name in members:
            out.census["n_read"] += 1
            try:
                raw = json.loads(zf.read(name))
                parse_study(raw, out)
            except Exception as e:  # noqa: BLE001 — every failure is recorded and listed
                out.census["n_parse_failures"] += 1
                out.rows["ingest_failures"].append((name, f"{type(e).__name__}: {str(e)[:500]}"))
    return out


def _flush(con: duckdb.DuckDBPyConnection, batch: Batch) -> None:
    for table, rows in batch.rows.items():
        if not rows:
            continue
        cols = list(zip(*rows, strict=True))
        tbl = pa.Table.from_arrays(
            [pa.array(col, type=f.type) for col, f in zip(cols, ARROW_SCHEMAS[table], strict=True)],
            schema=ARROW_SCHEMAS[table],
        )
        con.register("_batch", tbl)
        con.execute(f"INSERT INTO {table} SELECT * FROM _batch")
        con.unregister("_batch")
        rows.clear()


def list_members(zip_path: Path, limit: int | None = None) -> list[str]:
    with zipfile.ZipFile(zip_path) as zf:
        names = [i.filename for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".json")]
    names.sort()
    return names[:limit] if limit else names


def ingest(
    zip_path: Path,
    con: duckdb.DuckDBPyConnection,
    *,
    limit: int | None = None,
    workers: int | None = None,
    chunk_size: int = 2000,
    log=sys.stderr,
) -> dict[str, Any]:
    """Ingest `zip_path` into `con` (raw schema is (re)created). Returns the census dict."""
    t0 = time.monotonic()
    create_raw_schema(con, drop=True)
    members = list_members(zip_path, limit)
    n_members = len(members)
    chunks = [members[i : i + chunk_size] for i in range(0, n_members, chunk_size)]
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    print(f"ingest: {n_members:,} members in {len(chunks)} chunks, {workers} workers", file=log)

    total = Batch()
    pending = Batch()
    done = 0
    if workers == 1:
        results = (_parse_members(str(zip_path), ch) for ch in chunks)
        _drain(con, results, chunks, total, pending, t0, log)
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            results = ex.map(_parse_members, [str(zip_path)] * len(chunks), chunks)
            _drain(con, results, chunks, total, pending, t0, log)
    _flush(con, pending)
    done = total.census["n_read"]

    snapshot = con.execute("SELECT max(last_update_date_parsed) FROM studies").fetchone()[0]
    census: dict[str, Any] = dict(sorted(total.census.items()))
    census["n_members"] = n_members
    census["elapsed_s"] = round(time.monotonic() - t0, 1)
    failures = con.execute("SELECT member, error FROM ingest_failures ORDER BY member").fetchall()
    write_meta(
        con,
        {
            "snapshot_date": str(snapshot) if snapshot else "",
            "ingest_census": census,
            "ingest_failures": [{"member": m, "error": e} for m, e in failures],
            "source_zip": str(zip_path),
        },
    )
    print_census(census, failures, snapshot, log)
    assert done == n_members, f"read {done} of {n_members} members"
    return census


def _drain(con, results, chunks, total: Batch, pending: Batch, t0: float, log) -> None:
    n = 0
    for b in results:
        total.census.update(b.census)
        pending.extend(b)
        n += 1
        if sum(len(r) for r in pending.rows.values()) > 200_000:
            _flush(con, pending)
        if n % 10 == 0 or n == len(chunks):
            read = total.census["n_read"]
            rate = read / max(time.monotonic() - t0, 1e-6)
            print(f"\r  {read:,} studies  ({rate:,.0f}/s)", end="", file=log, flush=True)
    print(file=log)


def print_census(census: dict[str, Any], failures: list[tuple[str, str]], snapshot, log=sys.stderr) -> None:
    print("ingest census:", file=log)
    print(f"  snapshot_date (max lastUpdatePostDate in data): {snapshot}", file=log)
    for k, v in census.items():
        print(f"  {k:<36} {v:>12,}" if isinstance(v, int) else f"  {k:<36} {v}", file=log)
    if failures:
        print(f"  parse failures ({len(failures)}):", file=log)
        for m, e in failures[:50]:
            print(f"    {m}: {e}", file=log)
        if len(failures) > 50:
            print(f"    … +{len(failures) - 50} more (all in build_meta.ingest_failures)", file=log)
