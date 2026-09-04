"""Build the shipped fixtures from the full dump + a full-build DuckDB (spec §2.1).

  data/fixtures/mini.zip   ~200 studies hand/rule-picked to cover every §2.5 messiness case (pytest/CI)
  data/fixtures/demo.zip   every trial for the gold-set indications + a random sample (ctl build --demo)

Members are pruned (resultsSection, documentSection, contacts/locations, references, outcomes dropped)
so the demo slice stays tens of MB; the ingest never reads those modules anyway. A manifest JSON beside
each zip records the selection rules and the exact member count so tests can assert it.

Usage: uv run python scripts/make_fixtures.py [--zip data/raw/ctg-studies.json.zip] [--db data/ctg.duckdb]
"""

from __future__ import annotations

import argparse
import json
import random
import zipfile
from pathlib import Path

import duckdb

# MeSH ids of the gold-set indications (§8.3). Listed-string fallbacks catch trials without MeSH.
GOLD_INDICATIONS: dict[str, dict] = {
    "erdheim_chester": {"mesh": ["D031249"], "listed_like": ["erdheim%chester%"]},
    "geographic_atrophy": {"mesh": ["D057092"], "listed_like": ["geographic atrophy%"]},
    "multiple_myeloma": {"mesh": ["D009101"], "listed_like": ["multiple myeloma%"]},
    "ipf": {"mesh": ["D054990"], "listed_like": ["idiopathic pulmonary fibrosis%"]},
    "nsclc": {"mesh": ["D002289"], "listed_like": ["non%small%cell lung%", "nsclc%"]},
    "rcc": {"mesh": ["D002292"], "listed_like": ["renal cell carcinoma%", "renal cell cancer%"]},
}

# Always-in anchors for mini.zip (verified field shapes / spec examples)
MINI_ANCHORS = ["NCT02142738", "NCT02811861", "NCT02853331"]

# Rule-picked messiness cases for mini.zip: (name, sql returning nct_id, n)
MINI_RULES: list[tuple[str, str, int]] = [
    ("placebo_comparator", "SELECT DISTINCT nct_id FROM arms WHERE type='PLACEBO_COMPARATOR'", 15),
    ("sham_comparator", "SELECT DISTINCT nct_id FROM arms WHERE type='SHAM_COMPARATOR'", 4),
    (
        "other_typed_arms_only",
        """
        SELECT nct_id FROM arms GROUP BY nct_id HAVING count(*)>=1 AND bool_and(type='OTHER')""",
        8,
    ),
    ("single_arm", "SELECT nct_id FROM arms GROUP BY nct_id HAVING count(*)=1", 8),
    ("multi_arm_4plus", "SELECT nct_id FROM arms GROUP BY nct_id HAVING count(*)>=4", 6),
    (
        "combo_name_plus",
        r"SELECT DISTINCT nct_id FROM interventions WHERE regexp_matches(name_raw, '.+\s\+\s.+') AND type='DRUG'",
        8,
    ),
    (
        "combo_name_slash",
        r"SELECT DISTINCT nct_id FROM interventions WHERE regexp_matches(name_raw, '^[A-Za-z]+/[A-Za-z]+$') AND type='DRUG'",
        6,
    ),
    ("combination_product", "SELECT DISTINCT nct_id FROM interventions WHERE type='COMBINATION_PRODUCT'", 5),
    (
        "biological_with_other_names",
        """
        SELECT DISTINCT i.nct_id FROM interventions i JOIN intervention_other_names o USING (nct_id, intervention_no)
        WHERE i.type='BIOLOGICAL'""",
        10,
    ),
    (
        "many_other_names",
        """
        SELECT nct_id FROM intervention_other_names GROUP BY nct_id, intervention_no HAVING count(*)>=4""",
        8,
    ),
    (
        "dose_in_name",
        r"SELECT DISTINCT nct_id FROM interventions WHERE regexp_matches(name_raw, '\d+\s*(mg|mcg)') AND type='DRUG'",
        8,
    ),
    (
        "salt_in_name",
        r"SELECT DISTINCT nct_id FROM interventions WHERE regexp_matches(lower(name_raw), '(hydrochloride|mesylate|sodium|acetate)$') AND type='DRUG'",
        8,
    ),
    (
        "code_name",
        r"SELECT DISTINCT nct_id FROM interventions WHERE regexp_matches(name_raw, '^[A-Z]{1,5}[-\s]?\d{2,7}[A-Z]?$')",
        10,
    ),
    (
        "prefixed_name",
        r"SELECT DISTINCT nct_id FROM interventions WHERE regexp_matches(name_raw, '^(Drug|Biological|Experimental|Active Comparator):')",
        5,
    ),
    (
        "class_label_name",
        """
        SELECT DISTINCT nct_id FROM interventions WHERE lower(name_raw) IN
        ('chemotherapy','standard of care','corticosteroids','statins','immunotherapy','best supportive care','placebo','saline')""",
        10,
    ),
    ("observational", "SELECT nct_id FROM studies WHERE study_type='OBSERVATIONAL'", 10),
    ("expanded_access", "SELECT nct_id FROM studies WHERE study_type='EXPANDED_ACCESS'", 4),
    ("early_phase1", "SELECT nct_id FROM studies WHERE phase_norm='EARLY_PHASE1'", 3),
    ("phase4", "SELECT nct_id FROM studies WHERE phase_norm='PHASE4' AND study_type='INTERVENTIONAL'", 5),
    (
        "phase_na_interventional",
        "SELECT nct_id FROM studies WHERE phase_norm='NA' AND study_type='INTERVENTIONAL'",
        4,
    ),
    (
        "no_arms_but_interventions",
        """
        SELECT nct_id FROM studies WHERE nct_id NOT IN (SELECT nct_id FROM arms) AND nct_id IN (SELECT nct_id FROM interventions)""",
        6,
    ),
    (
        "month_precision_dates",
        "SELECT nct_id FROM studies WHERE date_precision='month' AND study_type='INTERVENTIONAL'",
        6,
    ),
    ("no_completion_date", "SELECT nct_id FROM studies WHERE completion_date IS NULL", 4),
    ("status_unknown", "SELECT nct_id FROM studies WHERE overall_status='UNKNOWN'", 4),
    (
        "status_terminated_withdrawn",
        "SELECT nct_id FROM studies WHERE overall_status IN ('TERMINATED','WITHDRAWN','SUSPENDED')",
        6,
    ),
    (
        "recruiting_industry_drug",
        """
        SELECT s.nct_id FROM studies s JOIN sponsors sp ON sp.nct_id=s.nct_id AND sp.role='lead' AND sp.agency_class='INDUSTRY'
        WHERE s.overall_status='RECRUITING' AND s.nct_id IN (SELECT nct_id FROM interventions WHERE type IN ('DRUG','BIOLOGICAL'))""",
        10,
    ),
    (
        "juvenile_or_pediatric_condition",
        "SELECT DISTINCT nct_id FROM study_conditions WHERE regexp_matches(lower(name_raw), '^(juvenile|pediatric|paediatric|childhood) ')",
        6,
    ),
    (
        "listed_only_no_mesh",
        """
        SELECT nct_id FROM study_conditions WHERE nct_id NOT IN (SELECT nct_id FROM mesh_terms WHERE module='condition')""",
        8,
    ),
    (
        "healthy_volunteers_condition",
        "SELECT DISTINCT nct_id FROM study_conditions WHERE regexp_matches(lower(name_raw), 'healthy')",
        4,
    ),
    (
        "mesh_id_artifact_condition",
        r"SELECT DISTINCT nct_id FROM study_conditions WHERE regexp_matches(name_raw, '^[CDcd]\d{5,7}$')",
        3,
    ),
    ("many_conditions", "SELECT nct_id FROM study_conditions GROUP BY nct_id HAVING count(*)>=6", 4),
    (
        "sponsor_suffix_variants",
        r"SELECT DISTINCT nct_id FROM sponsors WHERE role='lead' AND regexp_matches(name_raw, '(Inc\.|Ltd\.|GmbH|S\.A\.|, LLC|Pharmaceuticals)$')",
        8,
    ),
    (
        "curated_alias_sponsors",
        """
        SELECT DISTINCT nct_id FROM sponsors WHERE role='lead' AND (lower(name_raw) LIKE 'janssen%' OR lower(name_raw) LIKE 'merck sharp%'
        OR lower(name_raw) LIKE 'glaxo%' OR lower(name_raw) LIKE 'hoffmann-la roche%' OR lower(name_raw) LIKE 'genentech%')""",
        8,
    ),
    (
        "with_collaborators",
        "SELECT nct_id FROM sponsors WHERE role='collaborator' GROUP BY nct_id HAVING count(*)>=2",
        5,
    ),
    (
        "biomarker_in_title",
        "SELECT nct_id FROM studies WHERE regexp_matches(brief_title, '(EGFR|ALK|KRAS G12C|PD-L1|HER2|BRCA)')",
        8,
    ),
    (
        "subgroup_in_title",
        "SELECT nct_id FROM studies WHERE regexp_matches(lower(brief_title), '(first-line|relapsed|refractory|treatment-naive|metastatic|adolescent)')",
        8,
    ),
    (
        "device_or_behavioral",
        "SELECT DISTINCT nct_id FROM interventions WHERE type IN ('DEVICE','BEHAVIORAL','PROCEDURE')",
        6,
    ),
    ("missing_enrollment", "SELECT nct_id FROM studies WHERE enrollment_count IS NULL", 3),
]

PRUNE_TOP = {"resultsSection", "documentSection", "annotationSection"}
PRUNE_PROTOCOL = {
    "contactsLocationsModule",
    "referencesModule",
    "outcomesModule",
    "ipdSharingStatementModule",
}


def prune(raw: dict) -> dict:
    out = {k: v for k, v in raw.items() if k not in PRUNE_TOP}
    ps = out.get("protocolSection")
    if isinstance(ps, dict):
        ps = {k: v for k, v in ps.items() if k not in PRUNE_PROTOCOL}
        desc = ps.get("descriptionModule")
        if isinstance(desc, dict):  # detailedDescription is never ingested and is often the largest field
            ps["descriptionModule"] = {k: v for k, v in desc.items() if k != "detailedDescription"}
        ident = ps.get("identificationModule")
        if isinstance(ident, dict):
            ps["identificationModule"] = {k: v for k, v in ident.items() if k != "secondaryIdInfos"}
        out["protocolSection"] = ps
    return out


def _ids(con: duckdb.DuckDBPyConnection, sql: str) -> list[str]:
    return [r[0] for r in con.execute(sql).fetchall()]


def pick_mini(con: duckdb.DuckDBPyConnection, rng: random.Random) -> tuple[list[str], dict]:
    chosen: dict[str, list[str]] = {"anchors": list(MINI_ANCHORS)}
    picked: set[str] = set(MINI_ANCHORS)
    for name, sql, n in MINI_RULES:
        pool = _ids(con, sql)
        rng.shuffle(pool)
        take = [x for x in pool if x not in picked][:n]
        chosen[name] = take
        picked.update(take)
    return sorted(picked), chosen


def pick_demo(con: duckdb.DuckDBPyConnection, rng: random.Random, n_random: int) -> tuple[list[str], dict]:
    chosen: dict[str, int] = {}
    picked: set[str] = set()
    for key, spec in GOLD_INDICATIONS.items():
        mesh_list = ",".join(f"'{m}'" for m in spec["mesh"])
        likes = " OR ".join(f"lower(name_raw) LIKE '{p}'" for p in spec["listed_like"])
        ids = set(
            _ids(
                con,
                f"""
            SELECT DISTINCT nct_id FROM mesh_terms WHERE module='condition' AND kind='mesh' AND mesh_id IN ({mesh_list})
            UNION SELECT DISTINCT nct_id FROM study_conditions WHERE {likes}""",
            )
        )
        chosen[key] = len(ids)
        picked.update(ids)
    all_ids = _ids(con, "SELECT nct_id FROM studies")
    rng.shuffle(all_ids)
    extra = [x for x in all_ids if x not in picked][:n_random]
    chosen["random_sample"] = len(extra)
    picked.update(extra)
    return sorted(picked), chosen


def write_zip(src: zipfile.ZipFile, ids: list[str], dest: Path, prune_members: bool) -> int:
    members = {Path(n).stem: n for n in src.namelist() if n.lower().endswith(".json")}
    n = 0
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for nct in ids:
            name = members.get(nct)
            if not name:
                continue
            raw = src.read(name)
            if prune_members:
                raw = json.dumps(prune(json.loads(raw)), separators=(",", ":")).encode()
            zf.writestr(f"{nct}.json", raw)
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default="data/raw/ctg-studies.json.zip")
    ap.add_argument("--db", default="data/ctg.duckdb")
    ap.add_argument("--out", default="data/fixtures")
    ap.add_argument("--n-random", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=20260904)
    args = ap.parse_args()

    con = duckdb.connect(args.db, read_only=True)
    rng = random.Random(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    snapshot = con.execute("SELECT value FROM build_meta WHERE key='snapshot_date'").fetchone()[0]

    mini_ids, mini_sel = pick_mini(con, rng)
    demo_ids, demo_sel = pick_demo(con, rng, args.n_random)
    demo_ids = sorted(set(demo_ids) | set(mini_ids))  # demo ⊇ mini
    con.close()

    with zipfile.ZipFile(args.zip) as src:
        n_mini = write_zip(src, mini_ids, out / "mini.zip", prune_members=True)
        n_demo = write_zip(src, demo_ids, out / "demo.zip", prune_members=True)

    for name, n, sel in (("mini", n_mini, mini_sel), ("demo", n_demo, demo_sel)):
        manifest = {
            "n_members": n,
            "source_snapshot_date": snapshot,
            "seed": args.seed,
            "pruned": sorted(
                PRUNE_TOP
                | PRUNE_PROTOCOL
                | {"descriptionModule.detailedDescription", "identificationModule.secondaryIdInfos"}
            ),
            "selection": sel,
        }
        (out / f"{name}.manifest.json").write_text(json.dumps(manifest, indent=1))
        size = (out / f"{name}.zip").stat().st_size
        print(f"{name}.zip: {n:,} studies, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
