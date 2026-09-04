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
    con.execute(f"SET memory_limit='{memory_limit}'")
    con.execute("SET enable_external_access=false")
    con.execute("SET lock_configuration=true")
    return con


def create_raw_schema(con: duckdb.DuckDBPyConnection, drop: bool = False) -> None:
    if drop:
        for t in RAW_TABLES + ["build_meta"]:
            con.execute(f"DROP TABLE IF EXISTS {t}")
    con.execute(RAW_DDL)


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
