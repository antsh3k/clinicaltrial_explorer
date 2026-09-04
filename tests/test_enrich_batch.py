"""LLM-tier batch mechanics, fully offline: request shape, settling, cost, checkpoint, dry-run plan."""

import json
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from ct_landscape.db import apply_views, create_enrich_schema
from ct_landscape.enrich import batch
from ct_landscape.enrich.load import load_shipped_enrichment
from ct_landscape.enrich.prompts import SYSTEM
from ct_landscape.ingest import ingest
from ct_landscape.normalize.build import normalize

MINI = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "mini.zip"


@pytest.fixture(scope="module")
def con():
    con = duckdb.connect(":memory:")
    sink = open("/dev/null", "w")
    ingest(MINI, con, workers=1, log=sink)
    normalize(con, log=sink, workers=1)
    create_enrich_schema(con, drop=True)
    load_shipped_enrichment(con, Path("/nonexistent"), log=sink)
    apply_views(con, fail_on_empty=False)
    return con


def _msg(text, stop="end_turn"):
    return SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=700, output_tokens=120, cache_read_input_tokens=600, cache_creation_input_tokens=0
        ),
    )


def test_request_shape_caches_identical_system_block():
    asset = {
        "asset_id": "pembrolizumab",
        "canonical_name": "Pembrolizumab",
        "aliases": ["MK-3475"],
        "classes": [],
        "trials": [{"title": "T", "phase": "PHASE3", "conditions": ["NSCLC"]}],
    }
    req = batch._request(asset)
    assert req["custom_id"] == "pembrolizumab"
    p = req["params"]
    assert p["model"] == "claude-haiku-4-5" and p["temperature"] == 0.0
    assert p["system"][0]["text"] == SYSTEM and p["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert (
        "asset_id: pembrolizumab" in p["messages"][0]["content"] and "MK-3475" in p["messages"][0]["content"]
    )


def test_settle_paths():
    asset = {"asset_id": "x", "canonical_name": "X", "aliases": [], "classes": [], "trials": []}
    ok = batch._settle(
        asset, _msg('{"known_entity":"yes","basis":"well_known_drug","abstain":false}'), None, reask=False
    )
    assert ok["settled"] == "ok" and ok["enrichment"]["asset_id"] == "x"
    fenced = batch._settle(
        asset,
        _msg('```json\n{"known_entity":"no","basis":"insufficient","abstain":true}\n```'),
        None,
        reask=False,
    )
    assert fenced["settled"] == "ok" and fenced["enrichment"]["abstain"] is True
    bad = batch._settle(asset, _msg("Sorry, here is prose"), None, reask=False)
    assert bad["settled"] == "malformed" and bad["enrichment"] is None
    refused = batch._settle(asset, _msg("", stop="refusal"), None, reask=False)
    assert refused["settled"] == "refusal" and refused["enrichment"] is None  # a refusal is a settled abstain
    assert batch.cost_of(ok["usage"]) == pytest.approx(700 * 0.5e-6 + 120 * 2.5e-6 + 600 * 0.05e-6)


def test_checkpoint_is_append_only_last_wins(tmp_path):
    cp = tmp_path / "assets.jsonl"
    batch._append(cp, {"asset_id": "a", "enrichment": None, "settled": "malformed"})
    batch._append(cp, {"asset_id": "b", "enrichment": {"abstain": True}, "settled": "ok"})
    batch._append(cp, {"asset_id": "a", "enrichment": {"abstain": False}, "settled": "ok"})
    done = batch.load_checkpoint(cp)
    assert set(done) == {"a", "b"} and done["a"]["settled"] == "ok"
    assert len(cp.read_text().splitlines()) == 3  # nothing rewritten


def test_dry_run_plan_respects_ceiling_and_scope(con, tmp_path):
    assets = batch.in_scope_assets(con)
    assert assets and all(not a["asset_id"].count("+") for a in assets)  # combos excluded
    assert assets == sorted(assets, key=lambda a: (-a["n_trials"], a["asset_id"]))
    assert all(len(a["trials"]) <= 3 for a in assets)
    # a tiny ceiling selects few and reports the skipped tail visibly
    plan = batch.run(
        con, ceiling_usd=0.01, checkpoint=tmp_path / "cp.jsonl", dry_run=True, log=open("/dev/null", "w")
    )
    assert plan["n_selected"] + plan["n_skipped_over_budget"] == plan["n_todo"] == len(assets)
    assert plan["n_skipped_over_budget"] > 0 and plan["estimated_cost_usd"] <= 0.01
    # prior settled rows are excluded from todo
    cp = tmp_path / "cp2.jsonl"
    batch._append(
        cp,
        {
            "asset_id": assets[0]["asset_id"],
            "enrichment": None,
            "settled": "refusal",
            "usage": {"input": 10, "output": 1},
        },
    )
    plan2 = batch.run(con, ceiling_usd=35, checkpoint=cp, dry_run=True, log=open("/dev/null", "w"))
    assert plan2["n_already_settled"] == 1 and plan2["n_todo"] == len(assets) - 1
    assert json.dumps(plan2)  # serialisable for build_meta
