"""LLM tier — Anthropic Message Batches (spec §6.4–6.6). Abstain-first, checkpointed, budget-capped.

Mechanics:
  - scope: in-scope assets (industry-lead ∩ interventional ∩ drug/bio, role subject/unknown) WITHOUT a ChEMBL hit,
    ranked by trial count descending; `--limit` for the pilot; hard $ ceiling with a visible skip census
  - checkpoint: data/enrichment/assets.jsonl, append-only, keyed by asset_id, dedup-on-load (last settled wins).
    ONLY settled answers are written: a parsed JSON (valid or not) or a `refusal` stop reason (= settled abstain).
    Transport/batch errors are never checkpointed, so --resume retries them.
  - parsing: strict json.loads → Pydantic (in load.py); one non-batch re-ask on malformed JSON; else abstain-with-error
  - model: claude-haiku-4-5 (§6.6 budget); the system block is identical per request and cache-marked.
Nothing here runs unless `ctl enrich llm` is invoked explicitly; the pilot needs a human sign-off on spend.
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import duckdb

from ct_landscape.enrich.prompts import SYSTEM, user_block

CHECKPOINT = Path("data/enrichment/assets.jsonl")
MODEL = "claude-haiku-4-5"
# Batches API = 50% off list price (Haiku 4.5 list: $1 / $5 per MTok; cache reads ~0.1×)
PRICE_IN, PRICE_OUT, PRICE_CACHE_READ = 0.5e-6, 2.5e-6, 0.05e-6
DEFAULT_CEILING_USD = 35.0
EST_IN_TOKENS, EST_OUT_TOKENS = 750, 150  # §6.6 planning figures; the pilot measures the real ones


def in_scope_assets(
    con: duckdb.DuckDBPyConnection, limit: int | None = None, chembl_covered: bool = False
) -> list[dict]:
    """In-scope assets lacking a ChEMBL mechanism, with their prompt context, ranked by trial count desc.
    chembl_covered=True inverts the filter: assets that DO carry a ChEMBL label — the §8.1 agreement benchmark."""
    rows = con.execute(
        """
        WITH scope AS (
          SELECT ta.asset_id, count(DISTINCT ta.nct_id) AS n_trials
          FROM trial_assets ta JOIN v_trials t USING (nct_id)
          WHERE t.is_industry AND t.is_drug_trial AND t.study_type='INTERVENTIONAL' AND ta.role IN ('subject','unknown')
          GROUP BY 1
        )
        SELECT s.asset_id, a.canonical_name, s.n_trials,
               (SELECT list(alias_raw) FROM asset_aliases al WHERE al.asset_id = s.asset_id AND al.alias_key <> a.dedup_key) AS aliases,
               (SELECT list(class_term ORDER BY n_trials DESC) FROM asset_nlm_classes c WHERE c.asset_id = s.asset_id) AS classes
        FROM scope s JOIN assets a USING (asset_id)
        WHERE NOT a.is_combo AND s.asset_id """
        + ("IN" if chembl_covered else "NOT IN")
        + """ (SELECT asset_id FROM chembl_moa)
        ORDER BY """
        + (
            "hash(s.asset_id)" if chembl_covered else "s.n_trials DESC, s.asset_id"
        )  # agreement: a deterministic spread, not the top
        + (f" LIMIT {int(limit)}" if limit else "")
    ).fetchall()
    ids = [r[0] for r in rows]
    trials: dict[str, list[dict]] = defaultdict(list)
    if ids:
        con.register("_ids", __import__("pyarrow").table({"asset_id": ids}))
        for aid, title, phase, conds in con.execute(
            """
            WITH t AS (
              SELECT ta.asset_id, s.brief_title, v.phase_norm, v.phase_rank, v.last_update_date_parsed,
                     (SELECT list(display_name) FROM v_trial_conditions_primary tc WHERE tc.nct_id = s.nct_id) AS conds,
                     row_number() OVER (PARTITION BY ta.asset_id ORDER BY v.phase_rank DESC NULLS LAST, v.last_update_date_parsed DESC NULLS LAST) AS rn
              FROM trial_assets ta JOIN _ids USING (asset_id) JOIN studies s USING (nct_id) JOIN v_trials v USING (nct_id)
              WHERE ta.role IN ('subject','unknown') AND v.study_type='INTERVENTIONAL'
            )
            SELECT asset_id, brief_title, phase_norm, conds FROM t WHERE rn <= 3 ORDER BY asset_id, rn
            """
        ).fetchall():
            trials[aid].append({"title": title, "phase": phase, "conditions": conds or []})
        con.unregister("_ids")
    return [
        {
            "asset_id": r[0],
            "canonical_name": r[1],
            "n_trials": r[2],
            "aliases": [a for a in (r[3] or []) if a][:5],
            "classes": [c for c in (r[4] or []) if c][:5],
            "trials": trials.get(r[0], []),
        }
        for r in rows
    ]


def load_checkpoint(path: Path = CHECKPOINT) -> dict[str, dict]:
    done: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done[rec["asset_id"]] = rec
    return done


def _append(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def _request(asset: dict) -> dict:
    return {
        "custom_id": asset["asset_id"][:64],
        "params": {
            "model": MODEL,
            "max_tokens": 400,
            "temperature": 0.0,
            "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            "messages": [
                {
                    "role": "user",
                    "content": user_block(
                        asset["asset_id"],
                        asset["canonical_name"],
                        asset["aliases"],
                        asset["classes"],
                        asset["trials"],
                    ),
                }
            ],
        },
    }


def _parse_json(text: str) -> dict | None:
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _settle(asset: dict, message: Any, client, reask: bool) -> dict:
    """Turn a batch result message into a settled checkpoint record (or None → not settled)."""
    usage = {
        "input": message.usage.input_tokens,
        "output": message.usage.output_tokens,
        "cache_read": getattr(message.usage, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(message.usage, "cache_creation_input_tokens", 0) or 0,
    }
    base = {
        "asset_id": asset["asset_id"],
        "model": MODEL,
        "usage": usage,
        "settled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if message.stop_reason == "refusal":
        return {**base, "enrichment": None, "settled": "refusal"}  # a refusal IS a settled abstain
    text = next((b.text for b in message.content if b.type == "text"), "")
    obj = _parse_json(text)
    if obj is None and reask and client is not None:
        req = _request(asset)["params"]
        req["messages"].append({"role": "assistant", "content": text})
        req["messages"].append(
            {"role": "user", "content": "That was not valid JSON. Return ONLY the JSON object."}
        )
        try:
            m2 = client.messages.create(**req)
            usage["reask_input"] = m2.usage.input_tokens
            usage["reask_output"] = m2.usage.output_tokens
            obj = _parse_json(next((b.text for b in m2.content if b.type == "text"), ""))
        except Exception:  # noqa: BLE001
            obj = None
    if obj is None:
        return {**base, "enrichment": None, "settled": "malformed", "raw_text": text[:2000]}
    obj.setdefault("asset_id", asset["asset_id"])
    return {**base, "enrichment": obj, "settled": "ok"}


def cost_of(usage: dict) -> float:
    return (
        usage.get("input", 0) * PRICE_IN
        + usage.get("output", 0) * PRICE_OUT
        + usage.get("cache_read", 0) * PRICE_CACHE_READ
        + usage.get("reask_input", 0) * 2 * PRICE_IN
        + usage.get("reask_output", 0) * 2 * PRICE_OUT
    )


def run(
    con: duckdb.DuckDBPyConnection,
    *,
    limit: int | None = None,
    ceiling_usd: float = DEFAULT_CEILING_USD,
    checkpoint: Path = CHECKPOINT,
    dry_run: bool = False,
    poll_s: int = 30,
    log=sys.stderr,
    chembl_covered: bool = False,
) -> dict[str, Any]:
    """Plan → (dry-run: print the plan) → submit one batch → poll → settle → append. Returns the census."""
    assets = in_scope_assets(con, limit, chembl_covered=chembl_covered)
    done = load_checkpoint(checkpoint)
    todo = [a for a in assets if a["asset_id"] not in done]
    spent = sum(cost_of(r.get("usage", {})) for r in done.values())
    est_each = EST_IN_TOKENS * PRICE_IN + EST_OUT_TOKENS * PRICE_OUT
    affordable = max(0, int((ceiling_usd - spent) / est_each))
    selected, skipped = todo[:affordable], todo[affordable:]
    census: dict[str, Any] = {
        "n_in_scope_without_chembl": len(assets),
        "n_already_settled": len(done),
        "n_todo": len(todo),
        "n_selected": len(selected),
        "n_skipped_over_budget": len(skipped),
        "spent_before_usd": round(spent, 2),
        "estimated_cost_usd": round(len(selected) * est_each, 2),
        "ceiling_usd": ceiling_usd,
        "model": MODEL,
    }
    print(f"llm tier plan: {json.dumps(census)}", file=log)
    if dry_run or not selected:
        return census

    import anthropic

    client = anthropic.Anthropic()
    by_id = {a["asset_id"][:64]: a for a in selected}
    batch = client.messages.batches.create(requests=[_request(a) for a in selected])
    print(f"batch {batch.id} submitted with {len(selected)} requests", file=log)
    while True:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        print(
            f"\r  {b.processing_status}: {b.request_counts.succeeded} ok / {b.request_counts.errored} err / {b.request_counts.processing} pending",
            end="",
            file=log,
            flush=True,
        )
        time.sleep(poll_s)
    print(file=log)
    outcomes: Counter = Counter()
    spent_now = 0.0
    for res in client.messages.batches.results(batch.id):
        asset = by_id.get(res.custom_id)
        if asset is None:
            continue
        if res.result.type != "succeeded":
            outcomes[f"batch_{res.result.type}"] += 1  # NOT checkpointed → --resume retries
            continue
        rec = _settle(asset, res.result.message, client, reask=True)
        rec["batch_id"] = batch.id
        _append(checkpoint, rec)
        outcomes[rec["settled"]] += 1
        spent_now += cost_of(rec["usage"])
    census.update({"batch_id": batch.id, "outcomes": dict(outcomes), "spent_now_usd": round(spent_now, 2)})
    print(f"llm tier: {dict(outcomes)} — ${spent_now:.2f} this run, ${spent + spent_now:.2f} total", file=log)
    return census
