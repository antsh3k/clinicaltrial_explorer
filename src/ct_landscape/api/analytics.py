"""Harness-computed analytics for the evidence dashboard (UI service layer; the agent never calls this).

Two read-only functions over the documented views, both returning the SQL they ran so every figure the UI
draws can be re-run verbatim in the SQL console:

  profile_trials(con, nct_ids)      → one row per trial for an evidence set (phase, status, sponsor, start year,
                                       assets + roles + best MoA tier, primary conditions, population mentions).
                                       The UI aggregates and cross-filters client-side. Numbers come from the
                                       index, never from the model.
  entity_landscape(con, kind, id)   → a compact landscape for a condition / drug / company entity the answer named:
                                       headline counts, bar breakdowns, one sponsor/condition × phase matrix, and a
                                       `reference` block (the definition-of-record numbers per related entity) that
                                       the UI lays next to the agent's table rows.

Invariants: missing ≠ zero (NULL phase/status/year are bucketed as "unknown", never dropped); no enumeration caps
beyond the request-size cap and the explicit top-N of a chart; a Phase 4 trial is a Phase 4 trial, never
"approved"; entity matching for the reference block is exact id / exact canonical name, never fuzzy.
"""

from __future__ import annotations

import json
import re
from typing import Any

import duckdb

NCT_RE = re.compile(r"^NCT\d{8}$")
MAX_PROFILE = 2000

PROFILE_SQL = """
SELECT t.nct_id, t.brief_title, t.study_type, t.phase_norm, t.phase_rank, t.overall_status,
       t.is_active_readout, t.program_exists, t.is_inactive, t.is_industry,
       t.lead_company_id, c.canonical_name AS lead_company_name, t.lead_agency_class,
       year(t.start_date_parsed) AS start_year, t.enrollment_count, t.has_results,
       (SELECT list(tc.display_name ORDER BY tc.display_name)
          FROM v_trial_conditions_primary tc WHERE tc.nct_id = t.nct_id) AS conditions,
       (SELECT list(struct_pack(asset_id := ta.asset_id, role := ta.role,
                                tier := (SELECT m.provenance FROM v_moa_best m WHERE m.asset_id = ta.asset_id LIMIT 1))
                    ORDER BY ta.asset_id)
          FROM (SELECT DISTINCT asset_id, role FROM trial_assets WHERE nct_id = t.nct_id AND via = 'name') ta) AS assets,
       (SELECT list(struct_pack(kind := pm.kind, term_id := pm.term_id, label := coalesce(pt.label, pm.term_id))
                    ORDER BY pm.kind, pm.term_id)
          FROM (SELECT DISTINCT kind, term_id FROM population_mentions WHERE nct_id = t.nct_id) pm
          LEFT JOIN population_terms pt USING (term_id)) AS populations
FROM v_trials t
LEFT JOIN companies c ON c.company_id = t.lead_company_id
WHERE t.nct_id IN (SELECT unnest($1))
ORDER BY t.nct_id
"""


def profile_trials(con: duckdb.DuckDBPyConnection, nct_ids: list[str]) -> dict[str, Any]:
    ids = sorted({n for n in nct_ids if isinstance(n, str) and NCT_RE.match(n)})
    if len(ids) > MAX_PROFILE:
        raise ValueError(f"at most {MAX_PROFILE} trials per profile")
    rows: list[dict[str, Any]] = []
    if ids:
        cur = con.execute(PROFILE_SQL, [ids])
        cols = [d[0] for d in cur.description]
        rows = [{c: _json(v) for c, v in zip(cols, r, strict=True)} for r in cur.fetchall()]
    found = {r["nct_id"] for r in rows}
    shown_ids = "(" + ", ".join(f"'{n}'" for n in ids[:50]) + (", …" if len(ids) > 50 else "") + ")"
    return {
        "n_requested": len(ids),
        "n_found": len(rows),
        "missing": [n for n in ids if n not in found],
        "rows": rows,
        "sql": PROFILE_SQL.strip().replace("(SELECT unnest($1))", shown_ids),
    }


# ---------------------------------------------------------------- figure helpers

PHASE_CASE = (
    "CASE {col} WHEN 0.5 THEN 'Early Ph1' WHEN 1 THEN 'Phase 1' WHEN 2 THEN 'Phase 2' "
    "WHEN 3 THEN 'Phase 3' WHEN 4 THEN 'Phase 4' ELSE '{null}' END"
)
PHASE_ORDER = ["Early Ph1", "Phase 1", "Phase 2", "Phase 3", "Phase 4", "N/A", "unknown"]


def _shown(sql: str, params: list[Any]) -> str:
    """The query as the SQL console can re-run it: positional $n placeholders replaced by quoted literals."""
    out = sql.strip()
    for i, p in enumerate(params, 1):
        out = out.replace(f"${i}", "'" + str(p).replace("'", "''") + "'")
    return out


def _chart(
    con: duckdb.DuckDBPyConnection, title: str, sql: str, params: list[Any], note: str = ""
) -> dict[str, Any]:
    cur = con.execute(sql, params)
    items = [{"label": str(r[0]) if r[0] is not None else "unknown", "value": r[1]} for r in cur.fetchall()]
    return {"type": "bars", "title": title, "items": items, "note": note, "sql": _shown(sql, params)}


def _matrix(
    con: duckdb.DuckDBPyConnection, title: str, sql: str, params: list[Any], note: str = ""
) -> dict[str, Any]:
    """SQL returning (row_label, col_label, n) → a dense matrix; row order = first appearance (the SQL decides)."""
    cur = con.execute(sql, params)
    rows: list[str] = []
    cols: list[str] = []
    cells: dict[str, dict[str, Any]] = {}
    for r, c, n in cur.fetchall():
        r = str(r) if r is not None else "unknown"
        c = str(c) if c is not None else "unknown"
        if r not in cells:
            cells[r] = {}
            rows.append(r)
        if c not in cols:
            cols.append(c)
        cells[r][c] = n
    cols.sort(key=lambda c: (PHASE_ORDER.index(c) if c in PHASE_ORDER else 99, c))
    return {
        "type": "matrix",
        "title": title,
        "rows": rows,
        "cols": cols,
        "cells": cells,
        "note": note,
        "sql": _shown(sql, params),
    }


def _headline(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any]) -> tuple[dict[str, Any], str]:
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    return ({c: _json(v) for c, v in zip(cols, row, strict=True)} if row else {}, _shown(sql, params))


def _reference(
    con: duckdb.DuckDBPyConnection, kind: str, key: str, sql: str, params: list[Any], note: str
) -> dict[str, Any]:
    """Definition-of-record numbers per related entity, keyed by exact id AND exact canonical name (lower-cased)
    so the UI can lay them next to answer-table cells without any fuzzy matching."""
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    rows: dict[str, dict[str, Any]] = {}
    for r in cur.fetchall():
        rec = {c: _json(v) for c, v in zip(cols, r, strict=True)}
        rows[str(rec[key]).lower()] = rec
        if rec.get("name"):
            rows.setdefault(str(rec["name"]).lower(), rec)
    return {"kind": kind, "key": key, "rows": rows, "note": note, "sql": _shown(sql, params)}


def entity_landscape(con: duckdb.DuckDBPyConnection, kind: str, entity_id: str) -> dict[str, Any] | None:
    if kind == "condition":
        return _condition_landscape(con, entity_id)
    if kind == "drug":
        return _drug_landscape(con, entity_id)
    if kind == "company":
        return _company_landscape(con, entity_id)
    return None


# ---------------------------------------------------------------- condition


def _condition_landscape(con: duckdb.DuckDBPyConnection, key: str) -> dict[str, Any] | None:
    name = con.execute(
        "SELECT display_name, n_trials FROM v_conditions WHERE condition_key = $1", [key]
    ).fetchone()
    if not name:
        return None
    headline, hsql = _headline(
        con,
        """SELECT count(*) AS programs, count(*) FILTER (WHERE max_phase_active IS NOT NULL) AS active_programs,
                  count(DISTINCT lead_company_of_most_advanced) AS lead_companies,
                  sum(n_trials) AS program_trial_rows
           FROM v_programs WHERE condition_key = $1""",
        [key],
    )
    charts = [
        _chart(
            con,
            "Programs by most advanced ACTIVE phase",
            f"""SELECT {PHASE_CASE.format(col="max_phase_active", null="no active trial")} AS phase, count(*) AS n
               FROM v_programs WHERE condition_key = $1
               GROUP BY 1 ORDER BY min(coalesce(max_phase_active, -1)) DESC""",
            [key],
            "one row per asset × this condition (v_programs); trial-derived stage, not approval",
        ),
        _chart(
            con,
            "Most active lead sponsors (active trials)",
            """SELECT company_name || CASE WHEN agency_class = 'INDUSTRY' THEN '' ELSE ' (' || lower(agency_class) || ')' END,
                      n_active_trials
               FROM v_sponsor_condition WHERE condition_key = $1
               ORDER BY n_active_trials DESC, n_trials DESC, company_name LIMIT 10""",
            [key],
            "lead sponsor only; collaborators excluded (v_sponsor_condition)",
        ),
        _chart(
            con,
            "Mechanism label coverage of in-scope assets (by tier)",
            """WITH a AS (SELECT DISTINCT asset_id FROM v_programs WHERE condition_key = $1)
               SELECT coalesce(m.provenance, 'none') AS tier, count(*) AS n
               FROM a LEFT JOIN v_moa_best m USING (asset_id)
               GROUP BY 1 ORDER BY min(CASE m.provenance WHEN 'chembl' THEN 1 WHEN 'nlm_class' THEN 2 WHEN 'llm' THEN 3 ELSE 4 END)""",
            [key],
            "completeness of MoA answers for this indication: chembl (curated) > nlm_class > llm; 'none' is unlabeled",
        ),
        _chart(
            con,
            "Biomarkers & subgroups mentioned (top 12)",
            """SELECT pl.kind || ': ' || coalesce(pt.label, pl.term_id), pl.n_trials
               FROM v_population_landscape pl LEFT JOIN population_terms pt USING (term_id)
               WHERE pl.condition_key = $1 ORDER BY pl.n_trials DESC, 1 LIMIT 12""",
            [key],
            "lexicon mentions in eligibility text (recall-limited); inclusion vs exclusion is NOT parsed — verify via the trial card",
        ),
        _chart(
            con,
            "Interventional drug trials by start year",
            """SELECT coalesce(CAST(year(t.start_date_parsed) AS VARCHAR), 'unknown') AS yr, count(DISTINCT t.nct_id) AS n
               FROM v_trials t JOIN v_trial_conditions_primary tc USING (nct_id)
               WHERE tc.condition_key = $1 AND t.study_type = 'INTERVENTIONAL' AND t.is_drug_trial
               GROUP BY 1 ORDER BY 1""",
            [key],
            "start date as registered; 'unknown' = no start date",
        ),
        _matrix(
            con,
            "Lead sponsor × phase (top 10 sponsors by trials)",
            f"""WITH top AS (SELECT company_id, company_name, n_trials FROM v_sponsor_condition
                             WHERE condition_key = $1 ORDER BY n_trials DESC, company_name LIMIT 10)
               SELECT top.company_name, {PHASE_CASE.format(col="t.phase_rank", null="unknown")} AS phase,
                      count(DISTINCT t.nct_id) AS n
               FROM v_trials t
               JOIN v_trial_conditions_primary tc USING (nct_id)
               JOIN top ON top.company_id = t.lead_company_id
               WHERE tc.condition_key = $1 AND t.study_type = 'INTERVENTIONAL' AND t.is_drug_trial
               GROUP BY top.company_name, top.n_trials, 2 ORDER BY top.n_trials DESC, top.company_name, 2""",
            [key],
            "interventional drug trials led by the sponsor in this condition; 'unknown' = no phase recorded",
        ),
    ]
    reference = _reference(
        con,
        "drug",
        "asset_id",
        """SELECT p.asset_id, a.canonical_name AS name, p.n_trials, p.n_active_trials,
                  p.max_phase_ever, p.max_phase_active, c.canonical_name AS lead_company_of_most_advanced
           FROM v_programs p JOIN assets a USING (asset_id)
           LEFT JOIN companies c ON c.company_id = p.lead_company_of_most_advanced
           WHERE p.condition_key = $1""",
        [key],
        "v_programs: subject/unknown-role interventional trials of the asset in this condition — the Q1/Q2 definition of record",
    )
    return {
        "kind": "condition",
        "id": key,
        "name": name[0],
        "headline": {**headline, "trials_any_type": name[1]},
        "headline_sql": hsql,
        "charts": charts,
        "reference": reference,
    }


# ---------------------------------------------------------------- drug


def _drug_landscape(con: duckdb.DuckDBPyConnection, asset_id: str) -> dict[str, Any] | None:
    row = con.execute(
        "SELECT canonical_name, n_trials, n_subject_trials, n_comparator_trials, max_phase_any_role "
        "FROM v_assets WHERE asset_id = $1",
        [asset_id],
    ).fetchone()
    if not row:
        return None
    moa = con.execute(
        "SELECT provenance, moa_label, targets FROM v_moa_best WHERE asset_id = $1 LIMIT 1", [asset_id]
    ).fetchone()
    headline, hsql = _headline(
        con,
        """SELECT count(*) AS conditions, count(*) FILTER (WHERE max_phase_active IS NOT NULL) AS active_conditions,
                  max(max_phase_ever) AS max_phase_ever, max(max_phase_active) AS max_phase_active
           FROM v_programs WHERE asset_id = $1""",
        [asset_id],
    )
    charts = [
        _chart(
            con,
            "Conditions by most advanced phase (top 10 by trials)",
            f"""SELECT c.display_name || ' · ' || {PHASE_CASE.format(col="p.max_phase_ever", null="unknown")}, p.n_trials
               FROM v_programs p JOIN v_conditions c USING (condition_key)
               WHERE p.asset_id = $1 ORDER BY p.n_trials DESC, p.max_phase_ever DESC LIMIT 10""",
            [asset_id],
            "subject/unknown-role interventional trials per condition (v_programs)",
        ),
        _chart(
            con,
            "Trials by role of this asset",
            """SELECT role, count(DISTINCT nct_id) AS n FROM trial_assets WHERE asset_id = $1 GROUP BY 1 ORDER BY 2 DESC""",
            [asset_id],
            "subject = investigated; comparator = control arm only; unknown = arm structure undecidable",
        ),
        _chart(
            con,
            "Lead sponsors (trials as subject)",
            """SELECT company_name || CASE WHEN originator_proxy THEN ' ★' ELSE '' END, n_trials
               FROM v_asset_sponsors WHERE asset_id = $1 ORDER BY n_trials DESC, first_start LIMIT 10""",
            [asset_id],
            "★ = earliest industry lead sponsor (originator PROXY, not ownership)",
        ),
        _chart(
            con,
            "Biomarkers & subgroups mentioned in its trials (top 12)",
            """SELECT pm.kind || ': ' || coalesce(pt.label, pm.term_id), count(DISTINCT pm.nct_id) AS n
               FROM trial_assets ta JOIN population_mentions pm USING (nct_id)
               LEFT JOIN population_terms pt USING (term_id)
               WHERE ta.asset_id = $1 GROUP BY 1 ORDER BY n DESC, 1 LIMIT 12""",
            [asset_id],
            "lexicon mentions in eligibility text (recall-limited); inclusion vs exclusion is NOT parsed",
        ),
        _chart(
            con,
            "Trials by start year",
            """SELECT coalesce(CAST(year(t.start_date_parsed) AS VARCHAR), 'unknown') AS yr, count(DISTINCT t.nct_id) AS n
               FROM trial_assets ta JOIN v_trials t USING (nct_id) WHERE ta.asset_id = $1 GROUP BY 1 ORDER BY 1""",
            [asset_id],
            "",
        ),
        _matrix(
            con,
            "Condition × phase (top 8 conditions by trials)",
            f"""WITH top AS (SELECT condition_key, n_trials FROM v_programs WHERE asset_id = $1
                             ORDER BY n_trials DESC, condition_key LIMIT 8)
               SELECT c.display_name, {PHASE_CASE.format(col="t.phase_rank", null="unknown")} AS phase,
                      count(DISTINCT t.nct_id) AS n
               FROM trial_assets ta
               JOIN v_trials t USING (nct_id)
               JOIN v_trial_conditions_primary tc USING (nct_id)
               JOIN top USING (condition_key) JOIN v_conditions c USING (condition_key)
               WHERE ta.asset_id = $1 AND ta.role IN ('subject', 'unknown') AND t.study_type = 'INTERVENTIONAL'
               GROUP BY c.display_name, top.n_trials, 2 ORDER BY top.n_trials DESC, c.display_name, 2""",
            [asset_id],
            "subject/unknown-role interventional trials; a trial with several primary conditions counts in each",
        ),
    ]
    reference = _reference(
        con,
        "condition",
        "condition_key",
        """SELECT p.condition_key, c.display_name AS name, p.n_trials, p.n_active_trials,
                  p.max_phase_ever, p.max_phase_active, co.canonical_name AS lead_company_of_most_advanced
           FROM v_programs p JOIN v_conditions c USING (condition_key)
           LEFT JOIN companies co ON co.company_id = p.lead_company_of_most_advanced
           WHERE p.asset_id = $1""",
        [asset_id],
        "v_programs: subject/unknown-role interventional trials of this asset per condition",
    )
    return {
        "kind": "drug",
        "id": asset_id,
        "name": row[0],
        "headline": {
            **headline,
            "trials_any_role": row[1],
            "subject_trials": row[2],
            "comparator_trials": row[3],
            "moa": f"{moa[1]} [{moa[0]}]" if moa else "no mechanism label",
        },
        "headline_sql": hsql,
        "charts": charts,
        "reference": reference,
    }


# ---------------------------------------------------------------- company


def _company_landscape(con: duckdb.DuckDBPyConnection, company_id: str) -> dict[str, Any] | None:
    row = con.execute("SELECT canonical_name FROM companies WHERE company_id = $1", [company_id]).fetchone()
    if not row:
        return None
    headline, hsql = _headline(
        con,
        """SELECT sum(n_trials) AS drug_trials, sum(n_active_trials) AS active_trials, sum(n_phase3_plus) AS phase3_plus,
                  count(DISTINCT condition_key) AS conditions, max(latest_activity) AS latest_activity
           FROM v_sponsor_condition WHERE company_id = $1""",
        [company_id],
    )
    charts = [
        _chart(
            con,
            "Active trials by therapeutic area",
            """SELECT area, n_active_trials FROM v_sponsor_activity WHERE company_id = $1
               ORDER BY n_active_trials DESC, n_trials DESC LIMIT 10""",
            [company_id],
            "lead-sponsor interventional drug trials (v_sponsor_activity); a trial with several areas counts in each",
        ),
        _chart(
            con,
            "Top conditions (trials)",
            """SELECT c.display_name, s.n_trials FROM v_sponsor_condition s JOIN v_conditions c USING (condition_key)
               WHERE s.company_id = $1 ORDER BY s.n_trials DESC, c.display_name LIMIT 10""",
            [company_id],
            "",
        ),
        _chart(
            con,
            "Trials by phase",
            """SELECT coalesce(phase_norm, 'unknown'), count(*) FROM v_trials
               WHERE lead_company_id = $1 AND study_type = 'INTERVENTIONAL' AND is_drug_trial
               GROUP BY 1 ORDER BY 1""",
            [company_id],
            "",
        ),
        _chart(
            con,
            "Trials by start year",
            """SELECT coalesce(CAST(year(start_date_parsed) AS VARCHAR), 'unknown') AS yr, count(*) FROM v_trials
               WHERE lead_company_id = $1 AND study_type = 'INTERVENTIONAL' AND is_drug_trial
               GROUP BY 1 ORDER BY 1""",
            [company_id],
            "",
        ),
        _matrix(
            con,
            "Condition × phase (top 8 conditions by trials)",
            f"""WITH top AS (SELECT condition_key, n_trials FROM v_sponsor_condition WHERE company_id = $1
                             ORDER BY n_trials DESC, condition_key LIMIT 8)
               SELECT c.display_name, {PHASE_CASE.format(col="t.phase_rank", null="unknown")} AS phase,
                      count(DISTINCT t.nct_id) AS n
               FROM v_trials t JOIN v_trial_conditions_primary tc USING (nct_id)
               JOIN top USING (condition_key) JOIN v_conditions c USING (condition_key)
               WHERE t.lead_company_id = $1 AND t.study_type = 'INTERVENTIONAL' AND t.is_drug_trial
               GROUP BY c.display_name, top.n_trials, 2 ORDER BY top.n_trials DESC, c.display_name, 2""",
            [company_id],
            "lead-sponsor interventional drug trials per primary condition",
        ),
    ]
    reference = _reference(
        con,
        "condition",
        "condition_key",
        """SELECT s.condition_key, c.display_name AS name, s.n_trials, s.n_active_trials, s.n_phase3_plus, s.n_assets
           FROM v_sponsor_condition s JOIN v_conditions c USING (condition_key) WHERE s.company_id = $1""",
        [company_id],
        "v_sponsor_condition: lead-sponsor interventional drug trials per condition — the Q3 definition of record",
    )
    return {
        "kind": "company",
        "id": company_id,
        "name": row[0],
        "headline": headline,
        "headline_sql": hsql,
        "charts": charts,
        "reference": reference,
    }


def _json(v: Any) -> Any:
    """JSON-safe, LOSSLESS conversion (dates/decimals → str). Not the agent tool's _shrink: no list truncation here."""
    return json.loads(json.dumps(v, default=str))
