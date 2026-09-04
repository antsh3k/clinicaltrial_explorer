"""Stage 2 orchestrator — raw tables → entity/edge tables (spec §4.2, §5), deterministic and idempotent.

Every stage prints a census (n_in → n_out with per-reason drop counts) and writes it to build_meta.
Runs entirely from the raw tables of one DuckDB file; no network, no LLM.
"""

from __future__ import annotations

import os
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from typing import Any

import duckdb
import pyarrow as pa

from ct_landscape.db import create_entity_schema, write_meta
from ct_landscape.normalize.arms import assign_roles
from ct_landscape.normalize.assets import ASSET_TYPES, InterventionRow, build_assets
from ct_landscape.normalize.companies import _basic_norm, canonical_display, company_key
from ct_landscape.normalize.conditions import areas_for, denoise_reason, fold, unclassified_area
from ct_landscape.normalize.mechanism_key import mechanism_key
from ct_landscape.normalize.populations import entries, find_mentions


def _insert(con: duckdb.DuckDBPyConnection, table: str, rows: list[tuple], schema: pa.Schema) -> None:
    if not rows:
        return
    cols = list(zip(*rows, strict=True))
    tbl = pa.Table.from_arrays(
        [pa.array(c, type=f.type) for c, f in zip(cols, schema, strict=True)], schema=schema
    )
    con.register("_ins", tbl)
    con.execute(f"INSERT INTO {table} SELECT * FROM _ins")
    con.unregister("_ins")


def _log(log, msg: str) -> None:
    print(msg, file=log, flush=True)


# ---------------------------------------------------------------- assets + roles


def normalize_assets(con: duckdb.DuckDBPyConnection, log) -> dict[str, Any]:
    t0 = time.monotonic()
    types = ",".join(f"'{t}'" for t in ASSET_TYPES)
    ivs = [
        InterventionRow(*r)
        for r in con.execute(
            f"SELECT nct_id, intervention_no, type, name_raw FROM interventions WHERE type IN ({types}) AND name_raw IS NOT NULL ORDER BY 1, 2"
        ).fetchall()
    ]
    other: dict[tuple[str, int], list[str]] = defaultdict(list)
    for nct, no, name in con.execute(
        f"""SELECT o.nct_id, o.intervention_no, o.other_name_raw FROM intervention_other_names o
            JOIN interventions i USING (nct_id, intervention_no) WHERE i.type IN ({types}) AND o.other_name_raw IS NOT NULL"""
    ).fetchall():
        other[(nct, no)].append(name)
    res = build_assets(ivs, other)

    arm_links: dict[tuple[str, int], list[int]] = defaultdict(list)
    for nct, arm_no, no in con.execute(
        "SELECT nct_id, arm_no, intervention_no FROM arm_interventions"
    ).fetchall():
        arm_links[(nct, no)].append(arm_no)
    arm_types = {(n, a): t for n, a, t in con.execute("SELECT nct_id, arm_no, type FROM arms").fetchall()}
    n_arms = dict(con.execute("SELECT nct_id, count(*) FROM arms GROUP BY 1").fetchall())
    ta_rows = assign_roles(res.intervention_assets, arm_links, arm_types, n_arms)

    _insert(
        con,
        "assets",
        [(aid, a["canonical_name"], a["dedup_key"], a["is_combo"]) for aid, a in sorted(res.assets.items())],
        pa.schema(
            [
                ("asset_id", pa.string()),
                ("canonical_name", pa.string()),
                ("dedup_key", pa.string()),
                ("is_combo", pa.bool_()),
            ]
        ),
    )
    _insert(
        con,
        "asset_components",
        sorted(set(res.components)),
        pa.schema([("combo_asset_id", pa.string()), ("component_asset_id", pa.string())]),
    )
    _insert(
        con,
        "asset_aliases",
        [(k, v[0], v[1], v[2]) for k, v in sorted(res.aliases.items())],
        pa.schema(
            [
                ("alias_key", pa.string()),
                ("asset_id", pa.string()),
                ("alias_raw", pa.string()),
                ("source", pa.string()),
            ]
        ),
    )
    _insert(
        con,
        "contested_aliases",
        sorted(res.contested),
        pa.schema(
            [("alias_key", pa.string()), ("asset_ids", pa.list_(pa.string())), ("n_trials", pa.int32())]
        ),
    )
    _insert(
        con,
        "trial_assets",
        ta_rows,
        pa.schema(
            [
                ("nct_id", pa.string()),
                ("intervention_no", pa.int32()),
                ("asset_id", pa.string()),
                ("via", pa.string()),
                ("role", pa.string()),
                ("in_all_arms", pa.bool_()),
            ]
        ),
    )

    role_census = Counter(r[4] for r in ta_rows if r[3] == "name")
    census = {
        **{k: v for k, v in sorted(res.census.items())},
        "gates": dict(sorted(res.gate_census.items(), key=lambda kv: -kv[1])),
        "other_name_gates": dict(sorted(res.other_name_gate_census.items(), key=lambda kv: -kv[1])),
        "combo_parts_dropped": dict(sorted(res.dropped_parts_census.items(), key=lambda kv: -kv[1])),
        "roles": dict(role_census),
        "n_trial_asset_rows": len(ta_rows),
        "elapsed_s": round(time.monotonic() - t0, 1),
    }
    _log(
        log,
        f"assets: {res.census['n_interventions_in']:,} drug/bio interventions → "
        f"{res.census['n_interventions_keyed']:,} keyed, {res.census['n_interventions_gated']:,} gated → "
        f"{res.census['n_assets']:,} assets + {res.census['n_combo_assets']:,} combos; "
        f"{res.census['n_merged_via_other_names']:,} merged via otherNames; {res.census['n_contested_aliases']:,} contested; "
        f"roles {dict(role_census)} ({census['elapsed_s']}s)",
    )
    return census


# ---------------------------------------------------------------- conditions


def normalize_conditions(con: duckdb.DuckDBPyConnection, log) -> dict[str, Any]:
    t0 = time.monotonic()
    rows: list[tuple] = []
    denoised: list[tuple] = []
    reasons: Counter = Counter()
    display: dict[str, Counter] = defaultdict(Counter)
    n_listed_in = 0
    for nct, raw in con.execute(
        "SELECT nct_id, name_raw FROM study_conditions WHERE name_raw IS NOT NULL"
    ).fetchall():
        n_listed_in += 1
        f = fold(raw)
        r = denoise_reason(f)
        if r:
            reasons[r] += 1
            denoised.append((nct, raw, f, r))
            continue
        rows.append((nct, f, raw.strip(), "listed"))
        display[f][raw.strip()] += 1
    listed_rows = {(n, k, s): d for n, k, d, s in rows}
    mesh_rows = con.execute(
        "SELECT DISTINCT nct_id, mesh_id, term FROM mesh_terms WHERE module='condition' AND kind='mesh' AND mesh_id IS NOT NULL"
    ).fetchall()
    out = [(n, k, display[k].most_common(1)[0][0], "listed") for (n, k, _), _d in listed_rows.items()]
    out += [(n, mid, term, "mesh_leaf") for n, mid, term in mesh_rows]
    _insert(
        con,
        "trial_conditions_norm",
        out,
        pa.schema(
            [
                ("nct_id", pa.string()),
                ("condition_key", pa.string()),
                ("display_name", pa.string()),
                ("source", pa.string()),
            ]
        ),
    )
    _insert(
        con,
        "condition_denoised",
        denoised,
        pa.schema(
            [
                ("nct_id", pa.string()),
                ("name_raw", pa.string()),
                ("folded", pa.string()),
                ("reason", pa.string()),
            ]
        ),
    )

    # ---- area rollup per condition key. For a MeSH leaf, headings come from ancestors of trials carrying it;
    # prefer trials where it is the ONLY leaf (clean signal), else the intersection across all its trials.
    # The leaf's own term counts as a heading (top-level headings can themselves be leaves).
    leaf_trials: dict[str, list[str]] = defaultdict(list)
    n_leaves: Counter = Counter()
    leaf_term: dict[str, str] = {}
    for n, mid, term in mesh_rows:
        leaf_trials[mid].append(n)
        n_leaves[n] += 1
        leaf_term[mid] = term
    anc_by_trial: dict[str, set[str]] = defaultdict(set)
    for n, term in con.execute(
        "SELECT nct_id, term FROM mesh_terms WHERE module='condition' AND kind='ancestor' AND term IS NOT NULL"
    ).fetchall():
        anc_by_trial[n].add(term)
    area_rows: list[tuple] = []
    area_census: Counter = Counter()
    for mid, trials in leaf_trials.items():
        solo = [t for t in trials if n_leaves[t] == 1]
        if solo:
            headings = set.union(*(anc_by_trial.get(t, set()) for t in solo))
            src = "solo"
        else:
            headings = set.intersection(*(anc_by_trial.get(t, set()) for t in trials)) if trials else set()
            src = "intersection"
        headings.add(leaf_term[mid])
        areas = areas_for(sorted(headings))
        if not areas:
            areas = [(unclassified_area(), True)]
            area_census["mesh_leaf_unclassified"] += 1
        else:
            area_census[f"mesh_leaf_via_{src}"] += 1
        area_rows += [(mid, a, p) for a, p in areas]
    listed_keys = {k for _, k, _, s in out if s == "listed"}
    for k in listed_keys:
        area_rows.append((k, unclassified_area(), True))
    _insert(
        con,
        "condition_areas",
        sorted(set(area_rows)),
        pa.schema([("condition_key", pa.string()), ("area", pa.string()), ("is_primary", pa.bool_())]),
    )

    n_trials = con.execute("SELECT count(*) FROM studies").fetchone()[0]
    n_with_mesh = len({n for n, _, _ in mesh_rows})
    n_with_listed = len(
        {n for n, _, _, _ in out if _ == "listed"} if False else {r[0] for r in out if r[3] == "listed"}
    )
    census = {
        "n_listed_in": n_listed_in,
        "n_listed_kept": sum(1 for r in out if r[3] == "listed"),
        "denoise_reasons": dict(reasons),
        "n_mesh_leaf_rows": len(mesh_rows),
        "n_distinct_mesh_leaves": len(leaf_trials),
        "n_distinct_listed_keys": len(listed_keys),
        "n_trials": n_trials,
        "n_trials_with_mesh_leaf": n_with_mesh,
        "n_trials_listed_only": n_with_listed
        - len({r[0] for r in out if r[3] == "listed"} & {n for n, _, _ in mesh_rows}),
        "pct_trials_with_mesh_leaf": round(100 * n_with_mesh / max(n_trials, 1), 1),
        "areas": dict(area_census),
        "elapsed_s": round(time.monotonic() - t0, 1),
    }
    _log(
        log,
        f"conditions: {n_listed_in:,} listed strings → {census['n_listed_kept']:,} kept "
        f"(drops {dict(reasons)}); {len(leaf_trials):,} MeSH leaves on {n_with_mesh:,} trials "
        f"({census['pct_trials_with_mesh_leaf']}%); areas {dict(area_census)} ({census['elapsed_s']}s)",
    )
    return census


# ---------------------------------------------------------------- companies


def normalize_companies(con: duckdb.DuckDBPyConnection, log) -> dict[str, Any]:
    t0 = time.monotonic()
    raw_counts: dict[str, Counter] = defaultdict(Counter)
    tsn: list[tuple] = []
    alias_rows: dict[str, tuple[str, str]] = {}
    n_in = 0
    for nct, role, name, cls in con.execute(
        "SELECT nct_id, role, name_raw, agency_class FROM sponsors"
    ).fetchall():
        n_in += 1
        cid = company_key(name or "")
        raw_counts[cid][(name or "").strip()] += 1
        tsn.append((nct, role, cid, cls))
        ak = _basic_norm(name or "")
        if ak and ak not in alias_rows:
            alias_rows[ak] = (cid, (name or "").strip())
    companies = [
        (cid, canonical_display(cid, c.most_common(1)[0][0])) for cid, c in sorted(raw_counts.items())
    ]
    _insert(
        con, "companies", companies, pa.schema([("company_id", pa.string()), ("canonical_name", pa.string())])
    )
    _insert(
        con,
        "company_aliases",
        [(k, v[0], v[1]) for k, v in sorted(alias_rows.items())],
        pa.schema([("alias_key", pa.string()), ("company_id", pa.string()), ("alias_raw", pa.string())]),
    )
    _insert(
        con,
        "trial_sponsors_norm",
        tsn,
        pa.schema(
            [
                ("nct_id", pa.string()),
                ("role", pa.string()),
                ("company_id", pa.string()),
                ("agency_class", pa.string()),
            ]
        ),
    )
    n_raw = len({r for c in raw_counts.values() for r in c})
    census = {
        "n_sponsor_rows": n_in,
        "n_raw_names": n_raw,
        "n_companies": len(companies),
        "n_merged_names": n_raw - len(companies),
        "elapsed_s": round(time.monotonic() - t0, 1),
    }
    _log(
        log,
        f"companies: {n_in:,} sponsor rows, {n_raw:,} raw names → {len(companies):,} companies ({census['elapsed_s']}s)",
    )
    return census


# ---------------------------------------------------------------- populations (multiprocessing)


def _pop_worker(rows: list[tuple[str, str | None, list[str], str | None]]) -> list[tuple]:
    out = []
    for nct, title, conds, elig in rows:
        for m in find_mentions(title, conds, elig):
            out.append((nct, m.term_id, m.kind, m.surface, m.evidence_line))
    return out


def normalize_populations(con: duckdb.DuckDBPyConnection, log, workers: int | None = None) -> dict[str, Any]:
    t0 = time.monotonic()
    conds: dict[str, list[str]] = defaultdict(list)
    for nct, name in con.execute(
        "SELECT nct_id, name_raw FROM study_conditions WHERE name_raw IS NOT NULL ORDER BY nct_id, position"
    ).fetchall():
        conds[nct].append(name)
    studies = con.execute(
        "SELECT nct_id, brief_title, eligibility_criteria FROM studies ORDER BY nct_id"
    ).fetchall()
    rows = [(n, t, conds.get(n, []), e) for n, t, e in studies]
    chunk = 4000
    chunks = [rows[i : i + chunk] for i in range(0, len(rows), chunk)]
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    mentions: list[tuple] = []
    if workers == 1 or len(chunks) <= 1:
        for c in chunks:
            mentions += _pop_worker(c)
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for part in ex.map(_pop_worker, chunks):
                mentions += part
    _insert(
        con,
        "population_mentions",
        mentions,
        pa.schema(
            [
                ("nct_id", pa.string()),
                ("term_id", pa.string()),
                ("kind", pa.string()),
                ("surface", pa.string()),
                ("evidence_line", pa.string()),
            ]
        ),
    )
    _insert(
        con,
        "population_terms",
        [(e.term_id, e.kind, e.label) for e in entries()],
        pa.schema([("term_id", pa.string()), ("kind", pa.string()), ("label", pa.string())]),
    )
    by_kind = Counter()
    trials_by_kind: dict[str, set[str]] = defaultdict(set)
    for n, _t, kind, _s, _e in mentions:
        by_kind[kind] += 1
        trials_by_kind[kind].add(n)
    n_trials = len(rows)
    census = {
        "n_mentions": len(mentions),
        "mentions_by_kind": dict(by_kind),
        "pct_trials_with_mention_by_kind": {
            k: round(100 * len(v) / max(n_trials, 1), 1) for k, v in trials_by_kind.items()
        },
        "n_trials_with_any_mention": len(set().union(*trials_by_kind.values())) if trials_by_kind else 0,
        "elapsed_s": round(time.monotonic() - t0, 1),
    }
    _log(
        log,
        f"populations: {len(mentions):,} typed mentions on {census['n_trials_with_any_mention']:,} trials "
        f"{dict(by_kind)} ({census['elapsed_s']}s)",
    )
    return census


# ---------------------------------------------------------------- NLM pharmacologic classes (tier 2 of the MoA waterfall)

_CLASS_HINT = re.compile(
    r"(agents?|inhibitors?|antagonists?|agonists?|blockers?|modulators?|antibodies|antineoplastic|anti-|immunologic|"
    r"analgesics|anesthetics|antibiotics|antiviral|antifungal|antibacterial|enzyme|hormones?|vaccines?|immunosuppressive|"
    r"antidepressive|antipsychotic|anticonvulsants|hypoglycemic|antihypertensive|vasodilator|diuretics|anticoagulants|"
    r"fibrinolytic|bronchodilator|cholinergic|adrenergic|dopamine|serotonin|gaba|opioid|steroid|corticosteroid|"
    r"antirheumatic|immunomodulat|protein kinase|receptor|ligand|channel|reuptake|cytochrome|proteasome|topoisomerase|"
    r"tubulin|angiogenesis|checkpoint|vegf|egfr|jak|tnf|interleukin|interferon|cytokine|complement)",
    re.IGNORECASE,
)


def normalize_nlm_classes(con: duckdb.DuckDBPyConnection, log) -> dict[str, Any]:
    """interventionBrowseModule.ancestors carries NLM-curated pharmacologic classes for known drugs. We attach a class
    to an asset only when the trial's intervention MeSH leaf term matches one of the asset's alias keys exactly
    (the same fold as everywhere else) — never by co-occurrence alone. Classes are ancestor terms that look like
    pharmacologic classes (regex hint), counted per asset across trials."""
    from ct_landscape.normalize.drug_names import route

    t0 = time.monotonic()
    alias_to_asset = dict(con.execute("SELECT alias_key, asset_id FROM asset_aliases").fetchall())
    leaves = con.execute(
        "SELECT nct_id, mesh_id, term FROM mesh_terms WHERE module='intervention' AND kind='mesh' AND term IS NOT NULL"
    ).fetchall()
    trial_assets = defaultdict(set)
    for n, a in con.execute("SELECT nct_id, asset_id FROM trial_assets WHERE via='name'").fetchall():
        trial_assets[n].add(a)
    leaf_asset: dict[tuple[str, str], str] = {}
    n_leaf_matched = 0
    term_key_cache: dict[str, str | None] = {}
    for n, mid, term in leaves:
        if term not in term_key_cache:
            term_key_cache[term] = route(term).key
        k = term_key_cache[term]
        if not k:
            continue
        aid = alias_to_asset.get(k)
        if aid and aid in trial_assets.get(n, ()):
            leaf_asset[(n, mid)] = aid
            n_leaf_matched += 1
    # ancestors per trial (intervention module): attach to every matched asset in that trial only if the trial has
    # exactly ONE matched intervention leaf (otherwise ancestors are a union across drugs and would cross-contaminate)
    per_trial_assets: dict[str, set[str]] = defaultdict(set)
    for (n, _mid), aid in leaf_asset.items():
        per_trial_assets[n].add(aid)
    clean_trials = {n for n, s in per_trial_assets.items() if len(s) == 1}
    anc = con.execute(
        "SELECT nct_id, mesh_id, term FROM mesh_terms WHERE module='intervention' AND kind='ancestor' AND term IS NOT NULL"
    ).fetchall()
    counts: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for n, mid, term in anc:
        if n in clean_trials and _CLASS_HINT.search(term):
            (aid,) = per_trial_assets[n]
            counts[(aid, term, mid)].add(n)
    rows = [(aid, term, mid, len(ts), mechanism_key(term)) for (aid, term, mid), ts in counts.items()]
    _insert(
        con,
        "asset_nlm_classes",
        sorted(rows),
        pa.schema(
            [
                ("asset_id", pa.string()),
                ("class_term", pa.string()),
                ("mesh_id", pa.string()),
                ("n_trials", pa.int32()),
                ("moa_key", pa.string()),
            ]
        ),
    )
    census = {
        "n_intervention_mesh_leaves": len(leaves),
        "n_leaves_matched_to_assets": n_leaf_matched,
        "n_single_asset_trials_used": len(clean_trials),
        "n_asset_class_rows": len(rows),
        "n_assets_with_class": len({r[0] for r in rows}),
        "elapsed_s": round(time.monotonic() - t0, 1),
    }
    _log(
        log,
        f"nlm classes: {len(leaves):,} intervention MeSH leaves → {n_leaf_matched:,} matched to assets → "
        f"{census['n_assets_with_class']:,} assets carry ≥1 pharmacologic class ({census['elapsed_s']}s)",
    )
    return census


# ---------------------------------------------------------------- entry point


def normalize(con: duckdb.DuckDBPyConnection, log=sys.stderr, workers: int | None = None) -> dict[str, Any]:
    t0 = time.monotonic()
    create_entity_schema(con, drop=True)
    census: dict[str, Any] = {}
    census["assets"] = normalize_assets(con, log)
    census["conditions"] = normalize_conditions(con, log)
    census["companies"] = normalize_companies(con, log)
    census["populations"] = normalize_populations(con, log, workers=workers)
    census["nlm_classes"] = normalize_nlm_classes(con, log)
    census["elapsed_s"] = round(time.monotonic() - t0, 1)
    write_meta(con, {"normalize_census": census})
    return census
