"""API tests via TestClient with a scripted model — no network. Round-trips the answer store and permalinks."""

import json
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart

from ct_landscape.api.app import create_app
from ct_landscape.db import apply_views, create_enrich_schema
from ct_landscape.enrich.load import load_shipped_enrichment
from ct_landscape.evals.replay import scripted_model
from ct_landscape.funnel import compute_funnel
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
    compute_funnel(con)
    con.close()
    return str(path)


def _model():
    """resolve → run_sql → submit, carrying ids out of the results; on a follow-up turn, answer from history."""

    def fn(messages, info):
        returns = {}
        n_user = 0
        for m in messages:
            for p in getattr(m, "parts", []):
                if isinstance(p, ToolReturnPart):
                    returns[p.tool_name] = p.content
                if type(p).__name__ == "UserPromptPart":
                    n_user += 1
        if (
            n_user >= 2 and "submit_answer" in returns
        ):  # follow-up: cite an NCT retrieved in turn 1 (conversation-scoped gate)
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "submit_answer",
                        {
                            "answer_md": "As before: NCT02142738 is the Phase 3 KEYNOTE-024 trial.",
                            "citations": [{"nct_id": "NCT02142738", "why": "from turn 1"}],
                            "entities": [{"kind": "drug", "id": "pembrolizumab"}],
                            "caveats": [],
                        },
                    )
                ]
            )
        if "resolve_entity" not in returns:
            return ModelResponse(parts=[ToolCallPart("resolve_entity", {"query": "MK-3475", "kind": "drug"})])
        if "run_sql" not in returns:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_sql",
                        {"sql": "SELECT nct_id, phase_norm FROM v_trials WHERE nct_id = 'NCT02142738'"},
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "submit_answer",
                    {
                        "answer_md": "Pembrolizumab (MK-3475): see NCT02142738.",
                        "citations": [{"nct_id": "NCT02142738", "why": "KEYNOTE-024"}],
                        "entities": [{"kind": "drug", "id": "pembrolizumab"}],
                        "table": {"columns": ["nct", "phase"], "rows": [["NCT02142738", "PHASE3"]]},
                        "caveats": ["mini fixture"],
                    },
                )
            ]
        )

    return scripted_model(fn)


@pytest.fixture(scope="module")
def client(db_path, tmp_path_factory):
    app = create_app(db_path, runs_dir=tmp_path_factory.mktemp("runs"), model=_model())
    return TestClient(app)


def _sse_events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        ev, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if ev:
            out.append((ev, data))
    return out


def test_meta_and_static(client):
    m = client.get("/api/meta").json()
    assert m["n_studies"] > 200 and m["snapshot_date"] and "v_programs" in m["schema_card"]
    assert client.get("/").status_code == 200


def test_ask_streams_events_and_persists_permalink(client):
    cid = client.post("/api/conversations").json()["conversation_id"]
    r = client.post(f"/api/conversations/{cid}/ask", json={"question": "What is MK-3475?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(r.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "tool_call" and "gate" in kinds and "answer" in kinds and kinds[-1] == "done"
    answer = next(d for e, d in events if e == "answer")
    assert answer["gate"]["violations"] == [] and answer["answer_id"].startswith("a")
    assert answer["answer"]["table"]["rows"][0][0] == "NCT02142738"
    assert answer["coverage"] is not None and answer["context_turns"] == 0
    # permalink round-trip
    rec = client.get(f"/api/answers/{answer['answer_id']}").json()
    assert rec["question"] == "What is MK-3475?" and rec["answer"]["citations"][0]["nct_id"] == "NCT02142738"
    assert [t["tool"] for t in rec["trace"]] == ["resolve_entity", "run_sql"]
    # follow-up turn: cites an NCT retrieved in turn 1 without re-querying → gate is conversation-scoped
    r2 = client.post(f"/api/conversations/{cid}/ask", json={"question": "And which trial was that?"})
    ev2 = _sse_events(r2.text)
    a2 = next(d for e, d in ev2 if e == "answer")
    assert a2["gate"]["violations"] == [] and a2["context_turns"] == 1
    conv = client.get(f"/api/conversations/{cid}").json()
    assert len(conv["turns"]) == 2 and "messages" not in conv["turns"][0]


def test_unknown_conversation_and_answer(client):
    assert client.post("/api/conversations/nope-1234/ask", json={"question": "x"}).status_code == 404
    assert client.get("/api/answers/zzz").status_code == 404


def test_trial_resolve_and_sql_console(client):
    card = client.get("/api/trials/NCT02142738").json()
    assert card["ctgov_url"].endswith("NCT02142738") and card["arms"]
    assert client.get("/api/trials/NCT123").status_code == 400
    assert client.get("/api/trials/NCT00000000").status_code == 404
    res = client.get("/api/entities/resolve", params={"q": "MK-3475", "kind": "drug"}).json()
    assert res["candidates"][0]["id"] == "pembrolizumab"
    ok = client.post("/api/sql", json={"sql": "SELECT count(*) AS n FROM v_trials"}).json()
    assert ok["rows"][0][0] > 200
    bad = client.post("/api/sql", json={"sql": "DROP TABLE studies"})
    assert bad.status_code == 400 and "SELECT" in bad.json()["detail"]
    bad2 = client.post("/api/sql", json={"sql": "COPY (SELECT 1) TO '/tmp/x'"})
    assert bad2.status_code == 400


def test_index_page_declares_a_favicon_so_browsers_do_not_request_one(client):
    html = client.get("/").text
    assert 'rel="icon"' in html
