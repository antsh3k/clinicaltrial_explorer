"""Render the LLM-tier pilot for HAND-CHECKING (spec §8.1): a sample of settled rows with the exact context the
model saw, plus the pilot statistics (abstain rate, self-consistency, tokens, cost, target-validation rate) and,
when an agreement checkpoint exists, LLM-vs-ChEMBL target agreement on the curated head.

Usage: uv run python scripts/pilot_review.py [--checkpoint data/enrichment/assets.jsonl] [--n 30] [--seed 1]
       [--agreement data/enrichment/assets_agreement.jsonl] [--db data/ctg.duckdb] [--out runs/evals/pilot_review.md]
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path

import duckdb

from ct_landscape.enrich.batch import cost_of, load_checkpoint
from ct_landscape.enrich.models import AssetEnrichment


def _alias_key(s: str) -> str:
    return "".join(ch for ch in s.casefold() if ch.isalnum())


def stats(records: dict[str, dict], alias_to_symbol: dict[str, str]) -> dict:
    c: Counter = Counter()
    tokens_in = tokens_out = cache = 0
    cost = 0.0
    for r in records.values():
        c[r.get("settled", "?")] += 1
        u = r.get("usage") or {}
        tokens_in += u.get("input", 0)
        tokens_out += u.get("output", 0)
        cache += u.get("cache_read", 0)
        cost += cost_of(u)
        e = r.get("enrichment")
        if not e:
            continue
        try:
            m = AssetEnrichment.model_validate(e)
        except Exception:  # noqa: BLE001
            c["invalid"] += 1
            continue
        c["abstain" if m.abstain else "labeled"] += 1
        if not m.self_consistent:
            c["self_inconsistent"] += 1
        c[f"basis:{m.basis}"] += 1
        c[f"confidence:{m.confidence}"] += 1
        c[f"modality:{m.modality}"] += 1
        for t in m.targets:
            c["targets_total"] += 1
            if _alias_key(t) in alias_to_symbol:
                c["targets_validated"] += 1
    n = len(records)
    return {
        "n_settled": n,
        "outcomes": {k: v for k, v in c.items() if k in ("ok", "malformed", "refusal", "invalid")},
        "abstain_rate": round(c["abstain"] / max(c["abstain"] + c["labeled"], 1), 3),
        "self_inconsistent": c["self_inconsistent"],
        "basis": {k[6:]: v for k, v in c.items() if k.startswith("basis:")},
        "confidence": {k[11:]: v for k, v in c.items() if k.startswith("confidence:")},
        "modality": {k[9:]: v for k, v in c.items() if k.startswith("modality:")},
        "targets_unvalidated_rate": round(1 - c["targets_validated"] / max(c["targets_total"], 1), 3),
        "tokens_in_per_asset": round(tokens_in / max(n, 1)),
        "tokens_out_per_asset": round(tokens_out / max(n, 1)),
        "cache_read_per_asset": round(cache / max(n, 1)),
        "cost_usd": round(cost, 3),
        "cost_per_asset_usd": round(cost / max(n, 1), 5),
    }


def agreement(
    records: dict[str, dict], con: duckdb.DuckDBPyConnection, alias_to_symbol: dict[str, str]
) -> dict:
    """For assets that carry ChEMBL targets: does the LLM name at least one of them (after alias resolution)?"""
    chembl = {}
    for aid, syms in con.execute(
        "SELECT asset_id, list(DISTINCT s) FROM chembl_moa, unnest(target_symbols) AS u(s) GROUP BY 1"
    ).fetchall():
        chembl[aid] = set(syms)
    n = hit = abst = miss = 0
    rows = []
    for aid, r in records.items():
        if aid not in chembl or not r.get("enrichment"):
            continue
        try:
            m = AssetEnrichment.model_validate(r["enrichment"])
        except Exception:  # noqa: BLE001
            continue
        n += 1
        if m.abstain:
            abst += 1
            rows.append((aid, "abstain", sorted(chembl[aid])[:4], []))
            continue
        llm = {alias_to_symbol.get(_alias_key(t), t.upper()) for t in m.targets}
        ok = bool(llm & chembl[aid])
        hit += ok
        miss += not ok
        rows.append((aid, "agree" if ok else "DISAGREE", sorted(chembl[aid])[:4], sorted(llm)[:4]))
    return {
        "n": n,
        "agree": hit,
        "disagree": miss,
        "abstain": abst,
        "agreement_rate_excl_abstain": round(hit / max(hit + miss, 1), 3),
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", default="data/enrichment/assets.jsonl")
    ap.add_argument("--agreement", default="data/enrichment/assets_agreement.jsonl")
    ap.add_argument("--db", default="data/ctg.duckdb")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="runs/evals/pilot_review.md")
    args = ap.parse_args()

    con = duckdb.connect(args.db, read_only=True)
    alias_to_symbol = dict(con.execute("SELECT alias_key, symbol FROM target_aliases").fetchall())
    records = load_checkpoint(Path(args.checkpoint))
    st = stats(records, alias_to_symbol)
    lines = [
        "# LLM-tier pilot review",
        "",
        "## Statistics",
        "",
        "```json",
        json.dumps(st, indent=1),
        "```",
        "",
    ]

    ag_path = Path(args.agreement)
    if ag_path.exists():
        ag = agreement(load_checkpoint(ag_path), con, alias_to_symbol)
        lines += [
            "## ChEMBL agreement (curated head, held out from the pilot scope)",
            "",
            f"n={ag['n']} · agree={ag['agree']} · disagree={ag['disagree']} · abstain={ag['abstain']} · agreement excl. abstain={ag['agreement_rate_excl_abstain']}",
            "",
            "| asset | verdict | ChEMBL targets | LLM targets |",
            "|---|---|---|---|",
        ]
        lines += [f"| {a} | {v} | {', '.join(c)} | {', '.join(lt)} |" for a, v, c, lt in ag["rows"]]
        lines.append("")

    # hand-check sample: what the model saw + what it said
    rng = random.Random(args.seed)
    ids = sorted(records)
    sample = rng.sample(ids, min(args.n, len(ids)))
    lines += [
        f"## Hand-check sample ({len(sample)} of {len(records)}, seed {args.seed})",
        "",
        "Mark each row ✓ / ✗ / ? in the `verdict` column. Context = aliases + up to 3 trial titles the model saw.",
        "",
        "| # | asset (aliases) | trials seen | LLM: known / basis / conf | modality · action · targets · moa_class | abstain | verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, aid in enumerate(sample, 1):
        r = records[aid]
        name, aliases = con.execute(
            "SELECT canonical_name, (SELECT list(alias_raw) FROM asset_aliases al WHERE al.asset_id = a.asset_id) FROM assets a WHERE asset_id = ?",
            [aid],
        ).fetchone()
        trials = con.execute(
            """SELECT s.brief_title FROM trial_assets ta JOIN studies s USING (nct_id) JOIN v_trials v USING (nct_id)
               WHERE ta.asset_id = ? AND ta.role IN ('subject','unknown') ORDER BY v.phase_rank DESC NULLS LAST, v.last_update_date_parsed DESC NULLS LAST LIMIT 3""",
            [aid],
        ).fetchall()
        e = r.get("enrichment") or {}
        al = ", ".join(a for a in (aliases or []) if a and a.lower() != (name or "").lower())[:80]
        tr = " / ".join(t[0][:70] for t in trials)
        lines.append(
            f"| {i} | **{name}** ({al}) | {tr} | {e.get('known_entity', '—')} / {e.get('basis', '—')} / {e.get('confidence', '—')} | "
            f"{e.get('modality', '—')} · {e.get('action', '—')} · {', '.join(e.get('targets', []) or [])} · {e.get('moa_class') or '—'} | {e.get('abstain', r.get('settled'))} |  |"
        )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines) + "\n")
    print(json.dumps(st, indent=1))
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
