"""Load the SHIPPED enrichment artifacts (data/enrichment/*.jsonl) into the enrichment tables (spec §6).

Reviewers rebuild for $0: `ctl build` loads whatever ships. Absent files are a logged no-op.
  chembl_moa.jsonl  → asset_chembl + chembl_moa (+ targets / target_aliases seeded from the curated YAML and the
                      symbols present in the artifact)          [Phase 3a]
  assets.jsonl      → asset_enrichment (LLM tier, target-validated at load, §6.3)   [Phase 3b]
Rows whose asset_id no longer exists in this build (a rebuild changed a canonical key) are counted and skipped.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import duckdb
import pyarrow as pa

from ct_landscape.normalize.lexicons import load
from ct_landscape.normalize.mechanism_key import mechanism_key

ENRICHMENT_DIR = Path("data/enrichment")


def _insert(con, table, rows, schema):
    from ct_landscape.normalize.build import _insert as ins

    ins(con, table, rows, schema)


def _alias_key(s: str) -> str:
    return "".join(ch for ch in s.casefold() if ch.isalnum())


def seed_targets(con: duckdb.DuckDBPyConnection, extra_symbols: set[str]) -> None:
    """targets = curated aliases' symbols ∪ symbols present in the shipped ChEMBL artifact."""
    con.execute("DELETE FROM targets")
    con.execute("DELETE FROM target_aliases")
    target_rows: dict[str, tuple[str, str]] = {s: (s, "chembl") for s in extra_symbols}
    alias_rows: dict[str, tuple[str, str, str]] = {_alias_key(s): (s, s, "chembl") for s in extra_symbols}
    for row in load("target_aliases")["aliases"]:
        sym = row["symbol"]
        target_rows.setdefault(sym, (sym, "curated"))
        alias_rows[_alias_key(row["alias"])] = (sym, row["alias"], "curated")
        alias_rows.setdefault(_alias_key(sym), (sym, sym, "curated"))
    _insert(
        con,
        "targets",
        [(s, p, src) for s, (p, src) in sorted(target_rows.items())],
        pa.schema([("symbol", pa.string()), ("pref_name", pa.string()), ("source", pa.string())]),
    )
    _insert(
        con,
        "target_aliases",
        [(k, s, raw, src) for k, (s, raw, src) in sorted(alias_rows.items())],
        pa.schema(
            [
                ("alias_key", pa.string()),
                ("symbol", pa.string()),
                ("alias_raw", pa.string()),
                ("source", pa.string()),
            ]
        ),
    )


def load_chembl(con: duckdb.DuckDBPyConnection, path: Path, log=sys.stderr) -> Counter:
    c: Counter = Counter()
    known = {r[0] for r in con.execute("SELECT asset_id FROM assets").fetchall()}
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if "_attribution" in r:
            continue
        c["n_rows"] += 1
        if r["asset_id"] not in known:
            c["n_skipped_unknown_asset"] += 1
            continue
        rows.append(r)
    con.execute("DELETE FROM chembl_moa")
    con.execute("DELETE FROM asset_chembl")
    per_asset: dict[str, dict] = {}
    for r in rows:
        per_asset.setdefault(r["asset_id"], r)
    _insert(
        con,
        "asset_chembl",
        [
            (a, r["chembl_id"], r.get("chembl_pref_name"), r.get("matched_alias"), r.get("match_via"))
            for a, r in sorted(per_asset.items())
        ],
        pa.schema(
            [
                ("asset_id", pa.string()),
                ("chembl_id", pa.string()),
                ("chembl_pref_name", pa.string()),
                ("matched_alias", pa.string()),
                ("match_via", pa.string()),
            ]
        ),
    )
    seen: set[str] = set()
    moa = []
    for r in rows:
        if r["edge_key"] in seen:
            continue
        seen.add(r["edge_key"])
        syms = r.get("target_symbols") or []
        moa.append(
            (
                r["asset_id"],
                r.get("mechanism_of_action"),
                r.get("action_type"),
                syms,
                r.get("chembl_target_ids") or [],
                r["edge_key"],
                mechanism_key((r.get("mechanism_of_action") or "") + " " + " ".join(syms)),
            )
        )
    _insert(
        con,
        "chembl_moa",
        moa,
        pa.schema(
            [
                ("asset_id", pa.string()),
                ("mechanism_of_action", pa.string()),
                ("action_type", pa.string()),
                ("target_symbols", pa.list_(pa.string())),
                ("chembl_target_ids", pa.list_(pa.string())),
                ("edge_key", pa.string()),
                ("moa_key", pa.string()),
            ]
        ),
    )
    c["n_loaded_edges"] = len(moa)
    c["n_assets"] = len(per_asset)
    seed_targets(con, {s for r in rows for s in (r.get("target_symbols") or [])})
    return c


def load_llm(con: duckdb.DuckDBPyConnection, path: Path, log=sys.stderr) -> Counter:
    """LLM tier: strict Pydantic re-validation, self-consistency gate, target validation against the vocabulary."""
    from ct_landscape.enrich.models import AssetEnrichment

    c: Counter = Counter()
    known = {r[0] for r in con.execute("SELECT asset_id FROM assets").fetchall()}
    alias_to_symbol = dict(con.execute("SELECT alias_key, symbol FROM target_aliases").fetchall())
    con.execute("DELETE FROM asset_enrichment")
    rows = []
    seen: set[str] = set()
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        c["n_rows"] += 1
        aid = rec.get("asset_id")
        if aid in seen:  # append-only checkpoint: last settled answer wins
            c["n_duplicates_collapsed"] += 1
        seen.add(aid)
        if aid not in known:
            c["n_skipped_unknown_asset"] += 1
            continue
        model = rec.get("model")
        payload = rec.get("enrichment")
        if payload is None:
            c["n_abstain_with_error"] += 1
            rows.append(
                (aid, "unknown", [], [], "unknown", None, "low", True, "insufficient", model, json.dumps(rec))
            )
            continue
        try:
            e = AssetEnrichment.model_validate(payload)
        except Exception:  # noqa: BLE001 — a malformed row is an abstain-with-error, never a crash
            c["n_invalid_rows"] += 1
            rows.append(
                (aid, "unknown", [], [], "unknown", None, "low", True, "insufficient", model, json.dumps(rec))
            )
            continue
        abstained = e.abstain or not e.self_consistent
        if not e.self_consistent:
            c["n_self_inconsistent_as_abstain"] += 1
        canonical = []
        for t in e.targets:
            sym = alias_to_symbol.get(_alias_key(t))
            if sym:
                canonical.append(sym)
            else:
                c["n_targets_unvalidated"] += 1
            c["n_targets_total"] += 1
        rows.append(
            (
                aid,
                e.modality,
                list(e.targets),
                sorted(set(canonical)),
                e.action,
                e.moa_class,
                e.confidence,
                abstained,
                e.basis,
                model,
                json.dumps(rec),
            )
        )
    # last-wins dedup by asset_id
    last: dict[str, tuple] = {}
    for r in rows:
        last[r[0]] = r
    out = []
    for r in last.values():
        moa_key = "" if r[7] else mechanism_key((r[5] or "") + " " + " ".join(r[3]) + " " + " ".join(r[2]))
        out.append(r + (moa_key,))
    _insert(
        con,
        "asset_enrichment",
        out,
        pa.schema(
            [
                ("asset_id", pa.string()),
                ("modality", pa.string()),
                ("targets_raw", pa.list_(pa.string())),
                ("targets_canonical", pa.list_(pa.string())),
                ("action", pa.string()),
                ("moa_class", pa.string()),
                ("confidence", pa.string()),
                ("abstained", pa.bool_()),
                ("basis", pa.string()),
                ("model", pa.string()),
                ("raw_json", pa.string()),
                ("moa_key", pa.string()),
            ]
        ),
    )
    c["n_loaded"] = len(out)
    c["n_abstained"] = sum(1 for r in out if r[7])
    if c["n_targets_total"]:
        c["targets_unvalidated_rate"] = round(c["n_targets_unvalidated"] / c["n_targets_total"], 3)
    return c


def load_curated(con: duckdb.DuckDBPyConnection, log=sys.stderr) -> Counter:
    """Curated tier (lexicons/curated_moa.yaml): hand-written, cited, gene-level mechanisms for pipeline assets that
    ChEMBL/NLM do not carry yet. Entries name the asset by INN/code/brand and resolve through asset_aliases, so the
    same file serves every index; symbols are added to the targets vocabulary."""
    c: Counter = Counter()
    alias_to_asset = dict(con.execute("SELECT alias_key, asset_id FROM asset_aliases").fetchall())
    con.execute("DELETE FROM asset_curated_moa")
    rows = []
    seen: set[str] = set()
    for e in load("curated_moa")["entries"]:
        c["n_entries"] += 1
        names = [e["name"], *e.get("aliases", [])]
        hit = next(
            ((n, alias_to_asset[_alias_key(n)]) for n in names if _alias_key(n) in alias_to_asset), None
        )
        if hit is None:
            c["n_unresolved"] += 1
            continue
        matched, aid = hit
        if aid in seen:
            c["n_duplicate_asset"] += 1
            continue
        seen.add(aid)
        syms = list(e.get("targets", []))
        rows.append(
            (
                aid,
                e["moa"],
                e.get("action", "unknown"),
                syms,
                e.get("modality", "unknown"),
                matched,
                e.get("source", ""),
                mechanism_key(e["moa"] + " " + " ".join(syms)),
            )
        )
        for sym in syms:
            con.execute(
                "INSERT INTO targets SELECT ?, ?, 'curated' WHERE NOT EXISTS (SELECT 1 FROM targets WHERE symbol = ?)",
                [sym, sym, sym],
            )
            con.execute(
                "INSERT INTO target_aliases SELECT ?, ?, ?, 'curated' WHERE NOT EXISTS (SELECT 1 FROM target_aliases WHERE alias_key = ?)",
                [_alias_key(sym), sym, sym, _alias_key(sym)],
            )
    _insert(
        con,
        "asset_curated_moa",
        rows,
        pa.schema(
            [
                ("asset_id", pa.string()),
                ("moa_label", pa.string()),
                ("action", pa.string()),
                ("target_symbols", pa.list_(pa.string())),
                ("modality", pa.string()),
                ("matched_name", pa.string()),
                ("source_note", pa.string()),
                ("moa_key", pa.string()),
            ]
        ),
    )
    c["n_loaded"] = len(rows)
    return c


def load_shipped_enrichment(
    con: duckdb.DuckDBPyConnection, directory: Path = ENRICHMENT_DIR, log=sys.stderr
) -> dict:
    census: dict = {}
    chembl = directory / "chembl_moa.jsonl"
    llm = directory / "assets.jsonl"
    if chembl.exists():
        census["chembl"] = dict(load_chembl(con, chembl, log))
        print(f"enrichment: chembl tier loaded {census['chembl']}", file=log)
    else:
        seed_targets(con, set())
        print(
            "enrichment: no chembl_moa.jsonl shipped yet (Phase 3a) — targets seeded from curated aliases only",
            file=log,
        )
    census["curated"] = dict(load_curated(con, log))
    print(f"enrichment: curated tier loaded {census['curated']}", file=log)
    if llm.exists():
        census["llm"] = dict(load_llm(con, llm, log))
        print(f"enrichment: llm tier loaded {census['llm']}", file=log)
    else:
        print(
            "enrichment: no assets.jsonl shipped yet (Phase 3b) — v_moa carries chembl + nlm_class only",
            file=log,
        )
    from ct_landscape.db import write_meta

    write_meta(con, {"enrichment_census": census})
    return census
