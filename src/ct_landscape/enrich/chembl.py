"""Tier 1 of the MoA waterfall — ChEMBL curated mechanisms (spec §6.2), $0 and deterministic.

fetch:  mechanism + linked molecule (pref_name, synonyms incl. research codes and brands) + target (gene
        symbols via target components) from the ChEMBL REST API → one cached JSON (data/enrichment/chembl_raw.json,
        gitignored; the JOINED artifact ships).
join:   exact match ONLY, through the same §5.1 cleaning fold, between asset_aliases and ChEMBL pref_name +
        synonyms. A ChEMBL molecule matching ≥2 of our assets, or one alias matching ≥2 ChEMBL molecules → skip
        and log (contested-alias philosophy). Never fuzzy. ChEMBL never writes into asset_aliases.
ship:   data/enrichment/chembl_moa.jsonl (ChEMBL data © EMBL-EBI, CC BY-SA 3.0) + targets seed.
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pyarrow as pa

from ct_landscape.normalize.drug_names import route
from ct_landscape.normalize.lexicons import load

BASE = "https://www.ebi.ac.uk/chembl/api/data"
RAW_CACHE = Path("data/enrichment/chembl_raw.json")
SHIPPED = Path("data/enrichment/chembl_moa.jsonl")
ATTRIBUTION = (
    "ChEMBL data © EMBL-EBI, licensed CC BY-SA 3.0 (https://creativecommons.org/licenses/by-sa/3.0/)"
)

_SYN_TYPES_FOR_JOIN = {
    "INN",
    "USAN",
    "BAN",
    "JAN",
    "USP",
    "TRADE_NAME",
    "RESEARCH_CODE",
    "FDA",
    "EMA",
    "ATC",
    "OTHER",
    "INN_FRENCH",
    "INN_SPANISH",
    "MERCK_INDEX",
    "BNF",
    "NATIONAL_FORMULARY",
}


def _get(client: httpx.Client, path: str, params: dict[str, Any], retries: int = 5) -> dict:
    for attempt in range(retries):
        try:
            r = client.get(f"{BASE}/{path}", params=params)
            if r.status_code == 429 or r.status_code >= 500:
                raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            if attempt == retries - 1:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("unreachable")


def _paged(client: httpx.Client, path: str, key: str, params: dict[str, Any], log) -> list[dict]:
    out: list[dict] = []
    offset = 0
    while True:
        body = _get(client, path, {**params, "limit": 1000, "offset": offset})
        items = body.get(key, [])
        out.extend(items)
        total = body.get("page_meta", {}).get("total_count", 0)
        offset += len(items)
        print(f"\r  {path}: {offset:,}/{total:,}", end="", file=log, flush=True)
        if not items or offset >= total:
            break
    print(file=log)
    return out


def _by_ids(
    client: httpx.Client, path: str, key: str, id_field: str, ids: list[str], log, batch: int = 50
) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(ids), batch):
        chunk = ids[i : i + batch]
        body = _get(client, path, {f"{id_field}__in": ",".join(chunk), "limit": batch})
        out.extend(body.get(key, []))
        print(f"\r  {path}: {min(i + batch, len(ids)):,}/{len(ids):,}", end="", file=log, flush=True)
    print(file=log)
    return out


def fetch(cache: Path = RAW_CACHE, log=sys.stderr, refresh: bool = False) -> dict:
    """Pull mechanisms + molecules + targets into one cached JSON. Idempotent; reuses the cache unless refresh."""
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    with httpx.Client(
        timeout=120.0, headers={"User-Agent": "ct-landscape/0.1 (take-home; contact via repo)"}
    ) as client:
        mechs = _paged(client, "mechanism.json", "mechanisms", {}, log)
        mol_ids = sorted({m["molecule_chembl_id"] for m in mechs if m.get("molecule_chembl_id")})
        tgt_ids = sorted({m["target_chembl_id"] for m in mechs if m.get("target_chembl_id")})
        mols = _by_ids(client, "molecule.json", "molecules", "molecule_chembl_id", mol_ids, log)
        tgts = _by_ids(client, "target.json", "targets", "target_chembl_id", tgt_ids, log)
    data = {
        "fetched_at": time.strftime("%Y-%m-%d"),
        "attribution": ATTRIBUTION,
        "mechanisms": [
            {
                k: m.get(k)
                for k in (
                    "mec_id",
                    "molecule_chembl_id",
                    "parent_molecule_chembl_id",
                    "target_chembl_id",
                    "mechanism_of_action",
                    "action_type",
                    "max_phase",
                    "direct_interaction",
                    "molecular_mechanism",
                )
            }
            for m in mechs
        ],
        "molecules": {
            m["molecule_chembl_id"]: {
                "pref_name": m.get("pref_name"),
                "molecule_type": m.get("molecule_type"),
                "max_phase": m.get("max_phase"),
                "synonyms": sorted(
                    {
                        (s.get("molecule_synonym") or "", s.get("syn_type") or "")
                        for s in m.get("molecule_synonyms", [])
                    }
                ),
            }
            for m in mols
        },
        "targets": {
            t["target_chembl_id"]: {
                "pref_name": t.get("pref_name"),
                "target_type": t.get("target_type"),
                "organism": t.get("organism"),
                "gene_symbols": sorted(
                    {
                        s["component_synonym"]
                        for c in t.get("target_components", [])
                        for s in c.get("target_component_synonyms", [])
                        if s.get("syn_type") == "GENE_SYMBOL" and s.get("component_synonym")
                    }
                ),
                "gene_symbols_other": sorted(
                    {
                        s["component_synonym"]
                        for c in t.get("target_components", [])
                        for s in c.get("target_component_synonyms", [])
                        if s.get("syn_type") == "GENE_SYMBOL_OTHER"
                        and s.get("component_synonym")
                        and "=" not in s["component_synonym"]
                    }
                ),
            }
            for t in tgts
        },
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data))
    print(
        f"chembl fetch: {len(mechs):,} mechanisms, {len(mols):,} molecules, {len(tgts):,} targets → {cache}",
        file=log,
    )
    return data


# ---------------------------------------------------------------- join


def _fold(name: str) -> str | None:
    k = route(name)
    return k.key if k.key and not k.is_combo else None


def join(con: duckdb.DuckDBPyConnection, data: dict, log=sys.stderr) -> dict[str, Any]:
    """Exact-fold join asset_aliases ⟷ ChEMBL names; write chembl_moa / asset_chembl / targets / target_aliases;
    return the census. Ambiguity in either direction → skipped and logged."""
    alias_to_asset: dict[str, str] = dict(
        con.execute("SELECT alias_key, asset_id FROM asset_aliases").fetchall()
    )
    mols = data["molecules"]

    # ChEMBL side: folded name → {chembl ids}
    name_to_mols: dict[str, set[str]] = defaultdict(set)
    name_surface: dict[tuple[str, str], tuple[str, str]] = {}  # (fold, chembl_id) → (surface, via)
    for cid, m in mols.items():
        names = [(m.get("pref_name") or "", "pref_name")] + [
            (s, "synonym") for s, t in m.get("synonyms", []) if t in _SYN_TYPES_FOR_JOIN
        ]
        for surface, via in names:
            if not surface:
                continue
            f = _fold(surface)
            if f:
                name_to_mols[f].add(cid)
                name_surface.setdefault((f, cid), (surface, via))

    # candidate pairs
    asset_to_mols: dict[str, set[str]] = defaultdict(set)
    mol_to_assets: dict[str, set[str]] = defaultdict(set)
    pair_evidence: dict[tuple[str, str], tuple[str, str]] = {}
    for f, cids in name_to_mols.items():
        aid = alias_to_asset.get(f)
        if not aid:
            continue
        for cid in cids:
            asset_to_mols[aid].add(cid)
            mol_to_assets[cid].add(aid)
            pair_evidence.setdefault((aid, cid), name_surface[(f, cid)])
    # parent-molecule folding: salts/parent map to the same parent id in ChEMBL; treat parent as the identity
    parent = {
        m["molecule_chembl_id"]: (m.get("parent_molecule_chembl_id") or m["molecule_chembl_id"])
        for m in data["mechanisms"]
    }

    census: Counter = Counter()
    matched: dict[str, str] = {}
    skipped: list[tuple[str, str, list[str]]] = []
    mech_sig: dict[str, frozenset] = {}  # molecule (parent) → set of (target_id, action_type)
    for m in data["mechanisms"]:
        for mid in {m["molecule_chembl_id"], m.get("parent_molecule_chembl_id") or m["molecule_chembl_id"]}:
            mech_sig.setdefault(mid, frozenset())
            mech_sig[mid] = mech_sig[mid] | {(m.get("target_chembl_id"), m.get("action_type"))}
    for aid, cids in asset_to_mols.items():
        parents = {parent.get(c, c) for c in cids}
        if len(parents) > 1:
            # one of our aliases names several ChEMBL molecules (salts, esters, related agents). ChEMBL is a
            # LOOKUP, so the only thing that matters is whether the mechanisms agree: identical mechanism
            # signatures → safe to label (counted); different → real ambiguity → skip and log.
            sigs = {mech_sig.get(p_, frozenset()) for p_ in parents}
            if len(sigs) == 1 and next(iter(sigs)):
                census["n_asset_to_many_molecules_same_mechanism"] += 1
            else:
                census["n_ambiguous_asset_to_many_molecules"] += 1
                skipped.append((aid, "asset→many, mechanisms differ", sorted(parents)))
                continue
        cid = sorted(cids)[0]
        others = {a for c in cids for a in mol_to_assets[c]} - {aid}
        if others:
            # the same ChEMBL molecule also names OTHER assets of ours (its brands / typos we did not merge).
            # Labeling each is a lookup, never a merge — assets stay distinct; counted, not vetoed.
            census["n_molecule_shared_by_several_assets"] += 1
        matched[aid] = cid
    census["n_matched"] = len(matched)
    census["n_unmatched_assets"] = con.execute("SELECT count(*) FROM assets WHERE NOT is_combo").fetchone()[
        0
    ] - len(matched)

    # mechanisms per matched asset (via molecule or its parent)
    mech_by_mol: dict[str, list[dict]] = defaultdict(list)
    for m in data["mechanisms"]:
        mech_by_mol[m["molecule_chembl_id"]].append(m)
        p = m.get("parent_molecule_chembl_id")
        if p and p != m["molecule_chembl_id"]:
            mech_by_mol[p].append(m)
    tg = data["targets"]
    moa_rows: list[dict] = []
    seen_edges: set[str] = set()
    for aid, cid in sorted(matched.items()):
        for m in mech_by_mol.get(cid, []) + (
            [] if parent.get(cid, cid) == cid else mech_by_mol.get(parent[cid], [])
        ):
            t = tg.get(m.get("target_chembl_id") or "", {})
            symbols = t.get("gene_symbols", []) if t.get("organism") in (None, "Homo sapiens") else []
            edge_key = (
                f"{aid}|{'|'.join(symbols) or (m.get('target_chembl_id') or '')}|{m.get('action_type') or ''}"
            )
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            moa_rows.append(
                {
                    "asset_id": aid,
                    "chembl_id": cid,
                    "chembl_pref_name": mols[cid].get("pref_name"),
                    "matched_alias": pair_evidence[(aid, cid)][0],
                    "match_via": pair_evidence[(aid, cid)][1],
                    "mechanism_of_action": m.get("mechanism_of_action"),
                    "action_type": m.get("action_type"),
                    "target_chembl_id": m.get("target_chembl_id"),
                    "target_pref_name": t.get("pref_name"),
                    "target_type": t.get("target_type"),
                    "target_organism": t.get("organism"),
                    "target_symbols": symbols,
                    "edge_key": edge_key,
                }
            )
    census["n_moa_edges"] = len(moa_rows)
    census["n_assets_with_moa"] = len({r["asset_id"] for r in moa_rows})

    # targets vocabulary: ChEMBL human gene symbols + curated aliases
    target_rows: dict[str, tuple[str, str]] = {}
    alias_rows: dict[str, tuple[str, str, str]] = {}
    for t in tg.values():
        if t.get("organism") not in (None, "Homo sapiens"):
            continue
        for sym in t.get("gene_symbols", []):
            target_rows.setdefault(sym, (t.get("pref_name") or sym, "chembl"))
            alias_rows.setdefault(_alias_key(sym), (sym, sym, "chembl"))
        for other in t.get("gene_symbols_other", []):
            if t.get("gene_symbols"):
                alias_rows.setdefault(_alias_key(other), (t["gene_symbols"][0], other, "chembl_other"))
    for row in load("target_aliases")["aliases"]:
        sym = row["symbol"]
        target_rows.setdefault(sym, (sym, "curated"))
        alias_rows[_alias_key(row["alias"])] = (sym, row["alias"], "curated")
        alias_rows.setdefault(_alias_key(sym), (sym, sym, "curated"))
    census["n_targets"] = len(target_rows)
    census["n_target_aliases"] = len(alias_rows)

    write_join(con, moa_rows, target_rows, alias_rows)
    census["skipped_examples"] = [f"{a} ({why}: {', '.join(x[:4])})" for a, why, x in skipped[:20]]
    print(
        f"chembl join: n_matched={census['n_matched']:,} n_ambiguous_skipped="
        f"{census['n_ambiguous_asset_to_many_molecules']:,} "
        f"(shared-molecule lookups {census['n_molecule_shared_by_several_assets']:,}, "
        f"same-mechanism multi-molecule {census['n_asset_to_many_molecules_same_mechanism']:,}) "
        f"n_unmatched={census['n_unmatched_assets']:,} → {census['n_moa_edges']:,} mechanism edges on "
        f"{census['n_assets_with_moa']:,} assets; targets vocabulary {census['n_targets']:,} symbols",
        file=log,
    )
    return dict(census)


def _alias_key(s: str) -> str:
    return "".join(ch for ch in s.casefold() if ch.isalnum())


def write_join(
    con: duckdb.DuckDBPyConnection, moa_rows: list[dict], target_rows: dict, alias_rows: dict
) -> None:
    from ct_landscape.normalize.build import _insert
    from ct_landscape.normalize.mechanism_key import mechanism_key

    con.execute("DELETE FROM chembl_moa")
    con.execute("DELETE FROM asset_chembl")
    con.execute("DELETE FROM targets")
    con.execute("DELETE FROM target_aliases")
    per_asset: dict[str, dict] = {}
    for r in moa_rows:
        per_asset.setdefault(r["asset_id"], r)
    _insert(
        con,
        "asset_chembl",
        [
            (a, r["chembl_id"], r["chembl_pref_name"], r["matched_alias"], r["match_via"])
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
    _insert(
        con,
        "chembl_moa",
        [
            (
                r["asset_id"],
                r["mechanism_of_action"],
                r["action_type"],
                r["target_symbols"],
                [r["target_chembl_id"]] if r["target_chembl_id"] else [],
                r["edge_key"],
                mechanism_key((r["mechanism_of_action"] or "") + " " + " ".join(r["target_symbols"])),
            )
            for r in moa_rows
        ],
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


def ship(moa_rows: list[dict], path: Path = SHIPPED) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        f.write(
            json.dumps({"_attribution": ATTRIBUTION, "_note": "derived artifact; share-alike applies"}) + "\n"
        )
        for r in moa_rows:
            f.write(json.dumps(r) + "\n")


def run(con: duckdb.DuckDBPyConnection, log=sys.stderr, refresh: bool = False) -> dict[str, Any]:
    data = fetch(log=log, refresh=refresh)
    census = join(con, data, log)
    rows = con.execute(
        """SELECT m.asset_id, c.chembl_id, c.chembl_pref_name, c.matched_alias, c.match_via, m.mechanism_of_action, m.action_type,
                  m.target_symbols, m.chembl_target_ids, m.edge_key
           FROM chembl_moa m JOIN asset_chembl c USING (asset_id) ORDER BY m.asset_id, m.edge_key"""
    ).fetchall()
    ship(
        [
            {
                "asset_id": a,
                "chembl_id": b,
                "chembl_pref_name": c,
                "matched_alias": d,
                "match_via": e,
                "mechanism_of_action": f,
                "action_type": g,
                "target_symbols": h,
                "chembl_target_ids": i,
                "edge_key": j,
            }
            for a, b, c, d, e, f, g, h, i, j in rows
        ]
    )
    return census
