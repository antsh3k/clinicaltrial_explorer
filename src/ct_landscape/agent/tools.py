"""The agent's three read-only tools as plain functions over a sandboxed DuckDB connection (spec §7.2).

  resolve_entity — deterministic ladder (exact → alias → prefix → contains), NEVER fuzzy; kind=moa folds the
                   query through the App. B.7 mechanism key server-side; kind=condition prefers the MeSH key.
  run_sql        — four-layer sandbox: read-only connection + external access off + memory/time limits +
                   single SELECT/WITH statement after comment stripping. Rows capped at 200 for the model; ALL
                   NCT ids and entity ids in the full result are handed to the harness for the gate.
  get_trial      — one v_trial_card row.
agent.py wraps these as @agent.tool functions; api/ exposes them to the UI through the same code paths.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import duckdb
from pydantic import BaseModel

from ct_landscape.db import connect_sandboxed
from ct_landscape.normalize.companies import _basic_norm, company_key
from ct_landscape.normalize.conditions import MESH_ID, fold
from ct_landscape.normalize.drug_names import route
from ct_landscape.normalize.mechanism_key import mechanism_key

Kind = Literal["drug", "condition", "company", "moa", "population", "auto"]
ENTITY_KINDS = ("drug", "condition", "company", "moa", "population")
ROW_CAP = 200
LIST_HEAD = 10
STATEMENT_TIMEOUT_S = 20.0
NCT_RE = re.compile(r"^NCT\d{8}$")

# ---------------------------------------------------------------- connections


def open_sandboxed(db_path: str) -> duckdb.DuckDBPyConnection:
    """Layers 1–3 of the sandbox (§7.2). A FRESH connection per request/tool call; never shared."""
    return connect_sandboxed(db_path)


# ---------------------------------------------------------------- resolve_entity


class Candidate(BaseModel):
    id: str
    kind: str
    canonical_name: str
    n_trials: int
    matched_alias: str
    match: Literal["exact", "alias", "prefix", "contains"]


class ResolveResult(BaseModel):
    query: str
    kind: str
    candidates: list[Candidate]
    truncated: bool
    nearest: list[str]
    note: str = ""


_RESOLVERS: dict[str, dict[str, str]] = {
    # each: how the query is folded into a key, and the SQL surfaces to probe
    "drug": {
        "exact": """SELECT a.asset_id, a.canonical_name, v.n_trials, al.alias_raw, al.source
                    FROM asset_aliases al JOIN assets a USING (asset_id) JOIN v_assets v USING (asset_id)
                    WHERE al.alias_key = ? ORDER BY v.n_trials DESC LIMIT 10""",
        "prefix": """SELECT a.asset_id, a.canonical_name, v.n_trials, al.alias_raw, al.source
                     FROM asset_aliases al JOIN assets a USING (asset_id) JOIN v_assets v USING (asset_id)
                     WHERE al.alias_key LIKE ? || '%' ORDER BY v.n_trials DESC LIMIT 10""",
        "contains": """SELECT a.asset_id, a.canonical_name, v.n_trials, al.alias_raw, al.source
                       FROM asset_aliases al JOIN assets a USING (asset_id) JOIN v_assets v USING (asset_id)
                       WHERE al.alias_key LIKE '%' || ? || '%' ORDER BY v.n_trials DESC LIMIT 10""",
    },
    "condition": {
        "exact": """SELECT condition_key, display_name, n_trials, display_name, source FROM v_conditions
                    WHERE condition_key = ? OR lower(display_name) = ? ORDER BY (source = 'mesh_leaf') DESC, n_trials DESC LIMIT 10""",
        "prefix": """SELECT condition_key, display_name, n_trials, display_name, source FROM v_conditions
                     WHERE lower(display_name) LIKE ? || '%' ORDER BY (source = 'mesh_leaf') DESC, n_trials DESC LIMIT 10""",
        "contains": """SELECT condition_key, display_name, n_trials, display_name, source FROM v_conditions
                       WHERE lower(display_name) LIKE '%' || ? || '%' ORDER BY (source = 'mesh_leaf') DESC, n_trials DESC LIMIT 10""",
    },
    "company": {
        "exact": """SELECT c.company_id, c.canonical_name, count(DISTINCT t.nct_id) AS n, ca.alias_raw, 'alias'
                    FROM company_aliases ca JOIN companies c USING (company_id)
                    LEFT JOIN trial_sponsors_norm t ON t.company_id = c.company_id AND t.role = 'lead'
                    WHERE ca.alias_key = ? OR c.company_id = ? GROUP BY 1, 2, 4 ORDER BY n DESC LIMIT 10""",
        "prefix": """SELECT c.company_id, c.canonical_name, count(DISTINCT t.nct_id) AS n, min(ca.alias_raw), 'alias'
                     FROM company_aliases ca JOIN companies c USING (company_id)
                     LEFT JOIN trial_sponsors_norm t ON t.company_id = c.company_id AND t.role = 'lead'
                     WHERE c.company_id LIKE ? || '%' OR lower(c.canonical_name) LIKE ? || '%' GROUP BY 1, 2 ORDER BY n DESC LIMIT 10""",
        "contains": """SELECT c.company_id, c.canonical_name, count(DISTINCT t.nct_id) AS n, min(ca.alias_raw), 'alias'
                       FROM company_aliases ca JOIN companies c USING (company_id)
                       LEFT JOIN trial_sponsors_norm t ON t.company_id = c.company_id AND t.role = 'lead'
                       WHERE c.company_id LIKE '%' || ? || '%' GROUP BY 1, 2 ORDER BY n DESC LIMIT 10""",
    },
    "moa": {
        "exact": """SELECT moa_key, min(moa_label), count(DISTINCT asset_id), min(moa_label), min(provenance)
                    FROM v_moa WHERE moa_key = ? GROUP BY 1 LIMIT 10""",
        "prefix": """SELECT moa_key, min(moa_label), count(DISTINCT asset_id) AS n, min(moa_label), min(provenance)
                     FROM v_moa WHERE moa_key LIKE ? || '%' GROUP BY 1 ORDER BY n DESC LIMIT 10""",
        "contains": """SELECT moa_key, min(moa_label), count(DISTINCT asset_id) AS n, min(moa_label), min(provenance)
                       FROM v_moa WHERE moa_key LIKE '%' || ? || '%' GROUP BY 1 ORDER BY n DESC LIMIT 10""",
    },
    "population": {
        "exact": """SELECT pt.term_id, pt.label, count(DISTINCT pm.nct_id), pt.label, pt.kind
                    FROM population_terms pt LEFT JOIN population_mentions pm USING (term_id)
                    WHERE lower(pt.term_id) = ? OR lower(pt.label) = ? GROUP BY 1, 2, 5 LIMIT 10""",
        "prefix": """SELECT pt.term_id, pt.label, count(DISTINCT pm.nct_id) AS n, pt.label, pt.kind
                     FROM population_terms pt LEFT JOIN population_mentions pm USING (term_id)
                     WHERE lower(pt.term_id) LIKE ? || '%' OR lower(pt.label) LIKE ? || '%' GROUP BY 1, 2, 5 ORDER BY n DESC LIMIT 10""",
        "contains": """SELECT pt.term_id, pt.label, count(DISTINCT pm.nct_id) AS n, pt.label, pt.kind
                       FROM population_terms pt LEFT JOIN population_mentions pm USING (term_id)
                       WHERE lower(pt.label) LIKE '%' || ? || '%' GROUP BY 1, 2, 5 ORDER BY n DESC LIMIT 10""",
    },
}


def _keys_for(kind: str, query: str) -> dict[str, tuple]:
    """Query folds per rung. Returns rung → parameter tuple."""
    q = query.strip()
    if kind == "drug":
        k = route(q).key or re.sub(r"[^a-z0-9]", "", q.lower())
        return {"exact": (k,), "prefix": (k,), "contains": (k,)}
    if kind == "condition":
        f = fold(q)
        key = q.upper() if MESH_ID.match(q) else f
        return {"exact": (key, q.lower()), "prefix": (q.lower(),), "contains": (q.lower(),)}
    if kind == "company":
        ck = company_key(q)
        return {"exact": (_basic_norm(q), ck), "prefix": (ck, q.lower()), "contains": (ck,)}
    if kind == "moa":
        mk = mechanism_key(q)
        return {"exact": (mk,), "prefix": (mk,), "contains": (mk.split("|")[0] if mk else q.lower(),)}
    if kind == "population":
        return {"exact": (q.lower(), q.lower()), "prefix": (q.lower(), q.lower()), "contains": (q.lower(),)}
    raise ValueError(kind)


def resolve(con: duckdb.DuckDBPyConnection, query: str, kind: Kind = "auto") -> ResolveResult:
    kinds = list(ENTITY_KINDS) if kind == "auto" else [kind]
    found: list[Candidate] = []
    for k in kinds:
        params = _keys_for(k, query)
        for rung in ("exact", "alias", "prefix", "contains"):
            sql_rung = "exact" if rung == "alias" else rung
            if rung == "alias" and k != "drug":
                continue  # 'alias' is the exact rung reached through a non-canonical surface (drugs only)
            if not params[sql_rung][0]:
                continue
            rows = con.execute(_RESOLVERS[k][sql_rung], list(params[sql_rung])).fetchall()
            for rid, name, n, alias_raw, extra in rows:
                match = rung
                if k == "drug" and rung == "exact" and extra == "other_name":
                    match = "alias"
                if k == "drug" and rung == "alias":
                    continue
                if rid not in {c.id for c in found}:
                    found.append(
                        Candidate(
                            id=rid,
                            kind=k,
                            canonical_name=name or rid,
                            n_trials=int(n or 0),
                            matched_alias=str(alias_raw or ""),
                            match=match,
                        )
                    )
            if found and rung in ("exact", "alias"):
                break
        if found and kind != "auto":
            break
    found.sort(key=lambda c: (-c.n_trials, c.id))
    nearest: list[str] = []
    if not found:
        for k in kinds:
            params = _keys_for(k, query)
            probe = params["contains"][0][:4] if params["contains"][0] else ""
            if probe:
                rows = con.execute(_RESOLVERS[k]["contains"], [probe]).fetchall()[:5]
                nearest += [f"{k}:{r[0]} ({r[1]})" for r in rows]
    note = ""
    if kind == "condition" and found and found[0].id and not MESH_ID.match(found[0].id):
        note = "listed-only condition (no MeSH key); queries must stay in the listed keyspace"
    return ResolveResult(
        query=query,
        kind=kind,
        candidates=found[:10],
        truncated=len(found) > 10,
        nearest=nearest[:8],
        note=note,
    )


# ---------------------------------------------------------------- run_sql (layer 4 + result shaping)

_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|copy|export|import|install|load|pragma|set|reset|call|"
    r"begin|commit|rollback|vacuum|checkpoint|read_csv|read_json|read_parquet|read_text|read_blob|glob|"
    r"httpfs|sqlite_scan|postgres_scan|duckdb_settings|getenv)\b",
    re.IGNORECASE,
)


class SqlRejected(Exception):
    pass


def validate_sql(sql: str) -> str:
    s = _COMMENT_RE.sub(" ", sql).strip().rstrip(";").strip()
    if not s:
        raise SqlRejected("empty statement")
    if ";" in s:
        raise SqlRejected("exactly one statement allowed")
    if not re.match(r"^(select|with)\b", s, re.IGNORECASE):
        raise SqlRejected("statement must start with SELECT or WITH")
    m = _FORBIDDEN.search(s)
    if m:
        raise SqlRejected(f"forbidden token: {m.group(0)}")
    return s


@dataclass
class SqlResult:
    sql: str
    columns: list[str]
    rows: list[list[Any]]  # full result (uncapped) — harness use only
    total_row_count: int
    elapsed_ms: int
    nct_ids: set[str] = field(default_factory=set)
    entity_ids: set[str] = field(default_factory=set)

    def truncated(self) -> bool:
        return self.total_row_count > ROW_CAP

    def for_model(self) -> dict[str, Any]:
        """Row cap + list-column truncation; ALL ids were recorded before this."""
        out_rows = []
        for r in self.rows[:ROW_CAP]:
            out_rows.append([_shrink(v) for v in r])
        return {
            "columns": self.columns,
            "rows": out_rows,
            "total_row_count": self.total_row_count,
            "truncated": self.truncated(),
            "elapsed_ms": self.elapsed_ms,
            "note": "list columns show the first 10 items + '… (+N)'; every id in the FULL result is already grounded for citation",
        }


def _shrink(v: Any) -> Any:
    if isinstance(v, list):
        if len(v) > LIST_HEAD:
            return [_shrink(x) for x in v[:LIST_HEAD]] + [f"… (+{len(v) - LIST_HEAD})"]
        return [_shrink(x) for x in v]
    if isinstance(v, dict):
        return {k: _shrink(x) for k, x in v.items()}
    if isinstance(v, str) and len(v) > 400:
        return v[:400] + "…"
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    return str(v)


_ID_COLUMNS = {
    "asset_id",
    "partner_asset_id",
    "component_asset_id",
    "combo_asset_id",
    "condition_key",
    "company_id",
    "lead_company_id",
    "lead_company_of_most_advanced",
    "moa_key",
    "term_id",
}


def _harvest(value: Any, ncts: set[str], ents: set[str], col: str) -> None:
    if isinstance(value, str):
        if NCT_RE.match(value):
            ncts.add(value)
        elif col in _ID_COLUMNS:
            ents.add(value)
    elif isinstance(value, list):
        for x in value:
            _harvest(x, ncts, ents, col)
    elif isinstance(value, dict):
        for k, x in value.items():
            _harvest(x, ncts, ents, k)


def sandboxed_query(
    con: duckdb.DuckDBPyConnection, sql: str, timeout_s: float = STATEMENT_TIMEOUT_S
) -> SqlResult:
    """Validate (layer 4), execute with a wall-clock guard, harvest every NCT / entity id from the FULL result."""
    s = validate_sql(sql)
    t0 = time.monotonic()
    try:
        cur = con.execute(s)
        columns = [d[0] for d in cur.description]
        rows = [list(r) for r in cur.fetchall()]
    except duckdb.Error as e:
        raise SqlRejected(f"{type(e).__name__}: {str(e)[:600]}") from e
    elapsed = int((time.monotonic() - t0) * 1000)
    if elapsed > timeout_s * 1000:
        raise SqlRejected(f"query exceeded {timeout_s:.0f}s")
    ncts: set[str] = set()
    ents: set[str] = set()
    for r in rows:
        for col, v in zip(columns, r, strict=True):
            _harvest(v, ncts, ents, col)
    return SqlResult(
        sql=s,
        columns=columns,
        rows=rows,
        total_row_count=len(rows),
        elapsed_ms=elapsed,
        nct_ids=ncts,
        entity_ids=ents,
    )


# ---------------------------------------------------------------- get_trial


def get_trial(con: duckdb.DuckDBPyConnection, nct_id: str) -> dict[str, Any] | None:
    if not NCT_RE.match(nct_id or ""):
        raise SqlRejected("nct_id must match ^NCT\\d{8}$")
    cur = con.execute("SELECT * FROM v_trial_card WHERE nct_id = ?", [nct_id])
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return {c: _shrink(v) if c != "eligibility_criteria" else v for c, v in zip(cols, row, strict=True)}


def trial_entity_ids(card: dict[str, Any]) -> set[str]:
    ents: set[str] = set()
    for key in ("trial_assets", "conditions_primary", "population_mentions"):
        for item in card.get(key) or []:
            if isinstance(item, dict):
                for k in ("asset_id", "condition_key", "term_id"):
                    if item.get(k):
                        ents.add(item[k])
    if card.get("lead_company_id"):
        ents.add(card["lead_company_id"])
    return ents
