"""Agent-level tests with NO network: FunctionModel scripts drive the REAL tools and the REAL output validator.

- a scripted happy path: resolve → run_sql → submit_answer with ids carried out of the results
- the gate's one retry: a fabricated NCT is rejected, the corrected resubmission passes
- exhaustion: a model that never grounds its answer ends in an error event, never a clean answer
- TestModel smoke: the framework can drive every tool without crashing
"""

import asyncio
import json
from pathlib import Path

import duckdb
import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.models.test import TestModel

from ct_landscape.agent import tools as T
from ct_landscape.agent.agent import Deps, agent, answer_question
from ct_landscape.db import apply_views, create_enrich_schema
from ct_landscape.enrich.load import load_shipped_enrichment
from ct_landscape.evals.replay import scripted_model
from ct_landscape.ingest import ingest
from ct_landscape.normalize.build import normalize

MINI = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "mini.zip"


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "mini.duckdb"
    con = duckdb.connect(str(path))
    sink = open("/dev/null", "w")
    ingest(MINI, con, workers=1, log=sink)
    normalize(con, log=sink, workers=1)
    create_enrich_schema(con, drop=True)
    load_shipped_enrichment(con, Path("/nonexistent"), log=sink)
    apply_views(con, fail_on_empty=False)
    con.close()
    return str(path)


def _last_tool_returns(messages: list[ModelMessage]) -> dict[str, dict]:
    """tool_name → most recent tool return payload (the fenced dict)."""
    out: dict[str, dict] = {}
    for m in messages:
        for part in getattr(m, "parts", []):
            if isinstance(part, ToolReturnPart):
                out[part.tool_name] = part.content
    return out


def _scripted_model(fabricate_first: bool = False):
    """A model that behaves like a careful analyst: resolves, queries, then submits ids from the results."""
    state = {"n": 0}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        state["n"] += 1
        returns = _last_tool_returns(messages)
        if "resolve_entity" not in returns:
            return ModelResponse(parts=[ToolCallPart("resolve_entity", {"query": "MK-3475", "kind": "drug"})])
        if "run_sql" not in returns:
            aid = returns["resolve_entity"]["result"]["candidates"][0]["id"]
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_sql",
                        {
                            "sql": f"SELECT asset_id, condition_key, n_trials, nct_ids FROM v_programs WHERE asset_id = '{aid}' ORDER BY n_trials DESC"
                        },
                    )
                ]
            )
        rows = returns["run_sql"]["result"]["rows"]
        aid, ckey, _n, ncts = rows[0][0], rows[0][1], rows[0][2], rows[0][3]
        first_nct = ncts[0]
        # on the first submission, optionally plant a fabricated NCT; after a gate rejection, submit clean
        rejected = any(
            isinstance(p, ToolReturnPart) and "grounding gate" in str(p.content)
            for m in messages
            for p in getattr(m, "parts", [])
        )
        rejected = rejected or any(
            "grounding gate" in str(getattr(p, "content", ""))
            for m in messages
            for p in getattr(m, "parts", [])
        )
        nct = "NCT09999999" if (fabricate_first and not rejected) else first_nct
        answer = {
            "answer_md": f"{aid} has {len(rows)} program(s); e.g. {ckey} [{nct}].",
            "citations": [{"nct_id": nct, "why": "program row"}],
            "entities": [{"kind": "drug", "id": aid}, {"kind": "condition", "id": ckey}],
            "table": {"columns": ["asset", "condition", "nct"], "rows": [[aid, ckey, nct]]},
            "caveats": ["mini fixture"],
        }
        return ModelResponse(parts=[ToolCallPart("submit_answer", answer)])

    return scripted_model(fn)


async def _collect(db_path: str, model, question="What is MK-3475 in development for?"):
    con = T.open_sandboxed(db_path)
    deps = Deps(db=con)
    events = []
    async for ev in answer_question(deps, question, model=model):
        events.append(ev)
    con.close()
    return events, deps


def test_scripted_happy_path_runs_real_tools_and_gate(db_path):
    events, deps = asyncio.run(_collect(db_path, _scripted_model()))
    kinds = [e["event"] for e in events]
    assert kinds[:2] == ["tool_call", "tool_result"] and kinds[-1] == "done"
    assert "answer" in kinds and "error" not in kinds
    answer = next(e for e in events if e["event"] == "answer")
    assert answer["answer"]["entities"][0]["id"] == "pembrolizumab"
    assert answer["answer"]["citations"][0]["nct_id"] in deps.retrieved
    gate = next(e for e in events if e["event"] == "gate")
    assert gate["violations"] == [] and gate["verified"] == gate["checked"] == 3
    assert [t["tool"] for t in answer["trace"]] == ["resolve_entity", "run_sql"]
    assert json.dumps({k: v for k, v in answer.items() if k != "new_messages"})  # persistable


def test_gate_rejects_fabricated_nct_then_accepts_correction(db_path):
    events, deps = asyncio.run(_collect(db_path, _scripted_model(fabricate_first=True)))
    kinds = [e["event"] for e in events]
    assert "answer" in kinds and "error" not in kinds
    answer = next(e for e in events if e["event"] == "answer")
    assert "NCT09999999" not in answer["answer"]["answer_md"]
    # the run's message history shows the validator's retry prompt
    msgs = answer["new_messages"]
    assert any(
        "grounding gate" in str(getattr(p, "content", "")) for m in msgs for p in getattr(m, "parts", [])
    )


def test_never_grounded_answer_ends_in_error_not_a_clean_answer(db_path):
    def fn(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "submit_answer",
                    {
                        "answer_md": "See NCT09999999.",
                        "citations": [{"nct_id": "NCT09999999", "why": "x"}],
                        "entities": [],
                        "caveats": [],
                    },
                )
            ]
        )

    events, _ = asyncio.run(_collect(db_path, scripted_model(fn)))
    kinds = [e["event"] for e in events]
    assert "answer" not in kinds and "error" in kinds and kinds[-1] == "done"
    err = next(e for e in events if e["event"] == "error")
    assert err["gate"]["violations"]


def test_prose_cannot_end_a_run(db_path):
    calls = {"n": 0}

    def fn(messages, info):
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[TextPart("Here is my answer in prose, no tool call.")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "submit_answer",
                    {
                        "answer_md": "No trials found for that.",
                        "citations": [],
                        "entities": [],
                        "caveats": ["honest empty"],
                    },
                )
            ]
        )

    events, _ = asyncio.run(_collect(db_path, scripted_model(fn)))
    assert any(e["event"] == "answer" for e in events)
    assert calls["n"] >= 2  # the framework re-prompted for submit_answer


def test_testmodel_smoke_drives_every_tool(db_path):
    events, deps = asyncio.run(
        _collect(
            db_path,
            TestModel(
                call_tools=["get_trial"],
                custom_output_args={"answer_md": "x", "citations": [], "entities": [], "caveats": []},
            ),
        )
    )
    assert events[-1]["event"] == "done"
    assert any(e["event"] in ("answer", "error") for e in events)


def test_agent_override_with_testmodel_is_offline():
    with agent.override(
        model=TestModel(
            custom_output_args={"answer_md": "ok", "citations": [], "entities": [], "caveats": []}
        )
    ):
        pass  # construction + override never touch the network (defer_model_check=True)
