"""DuckDB connection helpers + raw-layer DDL (spec §4.1) + build_meta accessors.

Raw tables preserve the dump verbatim for ALL studies; the only derived columns are single-field pure
functions (`phase_norm`, `*_parsed` dates, `date_precision`). Scope filters live in views, never here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

DEFAULT_DB = Path("data/ctg.duckdb")

RAW_DDL = """
CREATE TABLE IF NOT EXISTS studies (
  nct_id TEXT PRIMARY KEY, brief_title TEXT, official_title TEXT,
  org_name TEXT, org_class TEXT,
  overall_status TEXT, study_type TEXT,
  phase_norm TEXT,
  enrollment_count INT, enrollment_type TEXT,
  start_date TEXT, primary_completion_date TEXT, completion_date TEXT, last_update_date TEXT,
  start_date_parsed DATE, primary_completion_date_parsed DATE, completion_date_parsed DATE,
  last_update_date_parsed DATE, date_precision TEXT,
  study_first_submit_date DATE,
  brief_summary TEXT, eligibility_criteria TEXT,
  healthy_volunteers BOOLEAN, sex TEXT, minimum_age TEXT, maximum_age TEXT, std_ages TEXT[],
  primary_purpose TEXT,
  has_results BOOLEAN
);
CREATE TABLE IF NOT EXISTS study_conditions        (nct_id TEXT, position INT, name_raw TEXT);
CREATE TABLE IF NOT EXISTS study_keywords          (nct_id TEXT, position INT, keyword_raw TEXT);
CREATE TABLE IF NOT EXISTS interventions           (nct_id TEXT, intervention_no INT, type TEXT, name_raw TEXT, description TEXT);
CREATE TABLE IF NOT EXISTS intervention_other_names(nct_id TEXT, intervention_no INT, other_name_raw TEXT);
CREATE TABLE IF NOT EXISTS arms                    (nct_id TEXT, arm_no INT, label TEXT, type TEXT, description TEXT);
CREATE TABLE IF NOT EXISTS arm_interventions       (nct_id TEXT, arm_no INT, intervention_no INT, via TEXT CHECK (via IN ('label','name')));
CREATE TABLE IF NOT EXISTS sponsors                (nct_id TEXT, role TEXT CHECK (role IN ('lead','collaborator')),
                                                    name_raw TEXT, agency_class TEXT);
CREATE TABLE IF NOT EXISTS mesh_terms              (nct_id TEXT, module TEXT CHECK (module IN ('condition','intervention')),
                                                    kind TEXT CHECK (kind IN ('mesh','ancestor')), mesh_id TEXT, term TEXT);
CREATE TABLE IF NOT EXISTS ingest_failures         (member TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS build_meta              (key TEXT PRIMARY KEY, value TEXT);
"""

ENTITY_DDL = """
CREATE TABLE IF NOT EXISTS assets            (asset_id TEXT PRIMARY KEY, canonical_name TEXT, dedup_key TEXT UNIQUE, is_combo BOOLEAN);
CREATE TABLE IF NOT EXISTS asset_components  (combo_asset_id TEXT, component_asset_id TEXT);
CREATE TABLE IF NOT EXISTS asset_aliases     (alias_key TEXT PRIMARY KEY, asset_id TEXT, alias_raw TEXT,
                                              source TEXT CHECK (source IN ('name','other_name')));
CREATE TABLE IF NOT EXISTS contested_aliases (alias_key TEXT, asset_ids TEXT[], n_trials INT,
                                              resolution TEXT);  -- 'vetoed' | 'dominance:<asset_id>'
CREATE TABLE IF NOT EXISTS trial_assets      (nct_id TEXT, intervention_no INT, asset_id TEXT,
                                              via TEXT CHECK (via IN ('name','combo_component')),
                                              role TEXT CHECK (role IN ('subject','comparator','unknown')),
                                              in_all_arms BOOLEAN);
CREATE TABLE IF NOT EXISTS companies         (company_id TEXT PRIMARY KEY, canonical_name TEXT);
CREATE TABLE IF NOT EXISTS company_aliases   (alias_key TEXT PRIMARY KEY, company_id TEXT, alias_raw TEXT);
CREATE TABLE IF NOT EXISTS trial_sponsors_norm (nct_id TEXT, role TEXT CHECK (role IN ('lead','collaborator')),
                                              company_id TEXT, agency_class TEXT);
CREATE TABLE IF NOT EXISTS trial_conditions_norm (nct_id TEXT, condition_key TEXT, display_name TEXT,
                                              source TEXT CHECK (source IN ('listed','mesh_leaf')));
CREATE TABLE IF NOT EXISTS condition_denoised (nct_id TEXT, name_raw TEXT, folded TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS condition_areas   (condition_key TEXT, area TEXT, is_primary BOOLEAN);
CREATE TABLE IF NOT EXISTS population_mentions (nct_id TEXT, term_id TEXT,
                                              kind TEXT CHECK (kind IN ('biomarker','demographic','disease_severity',
                                                                        'prior_therapy','line_of_therapy','disease_stage')),
                                              surface TEXT CHECK (surface IN ('title','condition','eligibility')),
                                              evidence_line TEXT);
CREATE TABLE IF NOT EXISTS population_terms  (term_id TEXT PRIMARY KEY, kind TEXT, label TEXT);
CREATE TABLE IF NOT EXISTS asset_nlm_classes (asset_id TEXT, class_term TEXT, mesh_id TEXT, n_trials INT, moa_key TEXT);
CREATE TABLE IF NOT EXISTS asset_chembl      (asset_id TEXT PRIMARY KEY, chembl_id TEXT, chembl_pref_name TEXT,
                                              matched_alias TEXT, match_via TEXT CHECK (match_via IN ('pref_name','synonym')));
CREATE TABLE IF NOT EXISTS chembl_moa        (asset_id TEXT, mechanism_of_action TEXT, action_type TEXT,
                                              target_symbols TEXT[], chembl_target_ids TEXT[], edge_key TEXT UNIQUE,
                                              moa_key TEXT);
CREATE TABLE IF NOT EXISTS targets           (symbol TEXT PRIMARY KEY, pref_name TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS target_aliases    (alias_key TEXT PRIMARY KEY, symbol TEXT, alias_raw TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS asset_enrichment  (asset_id TEXT PRIMARY KEY, modality TEXT,
                                              targets_raw TEXT[], targets_canonical TEXT[],
                                              action TEXT, moa_class TEXT, confidence TEXT, abstained BOOLEAN, basis TEXT,
                                              model TEXT, raw_json TEXT, moa_key TEXT);
"""

ENTITY_TABLES = [
    "assets",
    "asset_components",
    "asset_aliases",
    "contested_aliases",
    "trial_assets",
    "companies",
    "company_aliases",
    "trial_sponsors_norm",
    "trial_conditions_norm",
    "condition_denoised",
    "condition_areas",
    "population_mentions",
    "population_terms",
    "asset_nlm_classes",
]
ENRICH_TABLES = ["asset_chembl", "chembl_moa", "targets", "target_aliases", "asset_enrichment"]

RAW_TABLES = [
    "studies",
    "study_conditions",
    "study_keywords",
    "interventions",
    "intervention_other_names",
    "arms",
    "arm_interventions",
    "sponsors",
    "mesh_terms",
    "ingest_failures",
]


def connect(path: str | Path = DEFAULT_DB, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = Path(path)
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


def connect_sandboxed(path: str | Path = DEFAULT_DB, memory_limit: str = "2GB") -> duckdb.DuckDBPyConnection:
    """Read-only connection for agent / API / `ctl sql` use (spec §7.2 layers 1–2, part of 3).

    read_only protects the file; enable_external_access=false blocks read_csv/read_json/COPY/extension
    loads so a plain SELECT cannot read arbitrary local files; lock_configuration freezes the settings.
    """
    con = duckdb.connect(str(path), read_only=True)
    try:
        con.execute(f"SET memory_limit='{memory_limit}'")
        con.execute("SET enable_external_access=false")
        con.execute("SET lock_configuration=true")
    except duckdb.InvalidInputException:
        # DuckDB shares one database instance per file within a process: a second connection finds the
        # configuration already locked by the first sandboxed connection. That is fine ONLY if the lock
        # was applied with external access off — verify rather than assume.
        pass
    ext = con.execute("SELECT current_setting('enable_external_access')").fetchone()[0]
    if str(ext).lower() not in ("false", "0"):
        con.close()
        raise RuntimeError(
            "sandbox invariant violated: enable_external_access is on and the configuration is locked"
        )
    return con


def create_raw_schema(con: duckdb.DuckDBPyConnection, drop: bool = False) -> None:
    if drop:
        for t in RAW_TABLES + ["build_meta"]:
            con.execute(f"DROP TABLE IF EXISTS {t}")
    con.execute(RAW_DDL)


def create_entity_schema(con: duckdb.DuckDBPyConnection, drop: bool = False) -> None:
    if drop:
        for t in ENTITY_TABLES:
            con.execute(f"DROP TABLE IF EXISTS {t}")
    con.execute(ENTITY_DDL)


def create_enrich_schema(con: duckdb.DuckDBPyConnection, drop: bool = False) -> None:
    if drop:
        for t in ENRICH_TABLES:
            con.execute(f"DROP TABLE IF EXISTS {t}")
    con.execute(ENTITY_DDL)


VIEWS_SQL = Path(__file__).resolve().parent / "views.sql"


def apply_views(con: duckdb.DuckDBPyConnection, fail_on_empty: bool = True) -> dict[str, int]:
    """Apply views.sql; return {view: row_count}. Fails if any view returns 0 rows (spec §4.3)."""
    sql = VIEWS_SQL.read_text(encoding="utf-8")
    con.execute(sql)
    counts: dict[str, int] = {}
    views = [
        r[0]
        for r in con.execute(
            "SELECT view_name FROM duckdb_views() WHERE NOT internal AND schema_name='main'"
        ).fetchall()
    ]
    for v in views:
        counts[v] = con.execute(f"SELECT count(*) FROM {v}").fetchone()[0]
    empty = [v for v, n in counts.items() if n == 0]
    if empty and fail_on_empty:
        raise RuntimeError(f"empty views (a silently-empty definition is the likeliest real bug): {empty}")
    return counts


def write_meta(con: duckdb.DuckDBPyConnection, items: dict[str, Any]) -> None:
    for k, v in items.items():
        val = v if isinstance(v, str) else json.dumps(v, default=str)
        con.execute("INSERT OR REPLACE INTO build_meta VALUES (?, ?)", [k, val])


def read_meta(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in con.execute("SELECT key, value FROM build_meta").fetchall():
        try:
            out[k] = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            out[k] = v
    return out
