"""FastAPI service (spec §7.5): one process serves the API and the static chat frontend.

POST /api/conversations                      → {conversation_id}
POST /api/conversations/{id}/ask {question}  → SSE: tool_call · tool_result · note · gate · answer · error · done
GET  /api/answers/{answer_id}                → persisted answer + trace (permalink)
GET  /api/trials/{nct_id}                    → v_trial_card row
GET  /api/entities/resolve?q=&kind=          → resolve_entity
POST /api/sql {sql}                          → read-only console, the SAME sandbox as the agent tool
GET  /api/meta                               → snapshot date + build census (coverage footer)
No auth/CORS (single localhost process). A fresh sandboxed DuckDB connection per request; one in-flight run per conversation.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ct_landscape.agent import tools as T
from ct_landscape.agent.agent import Deps, answer_question
from ct_landscape.agent.schema_card import schema_card
from ct_landscape.api.store import Store
from ct_landscape.db import read_meta

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class SqlBody(BaseModel):
    sql: str = Field(min_length=1, max_length=20000)


def create_app(db_path: str, runs_dir: Path | None = None, model: Any | None = None) -> FastAPI:
    """`model` lets tests inject TestModel/FunctionModel; production uses the agent's configured model."""
    app = FastAPI(title="ct-landscape", version="0.1.0")
    store = Store(runs_dir) if runs_dir else Store()
    locks: dict[str, asyncio.Lock] = {}
    state = {"db_path": db_path, "model": model}

    def db():
        if not os.path.exists(state["db_path"]):
            raise HTTPException(503, f"index not found at {state['db_path']}; run `ctl build`")
        return T.open_sandboxed(state["db_path"])

    # ---- meta
    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        con = db()
        try:
            m = read_meta(con)
            n = con.execute("SELECT count(*) FROM studies").fetchone()[0]
            return {
                "snapshot_date": m.get("snapshot_date"),
                "n_studies": n,
                "funnel": m.get("funnel"),
                "model": str(state["model"] or "anthropic:claude-sonnet-5"),
                "schema_card": schema_card(con),
            }
        finally:
            con.close()

    # ---- conversations
    @app.post("/api/conversations")
    def new_conversation() -> dict[str, str]:
        return {"conversation_id": store.new_conversation()}

    @app.get("/api/conversations/{cid}")
    def get_conversation(cid: str) -> dict[str, Any]:
        if not store.exists(cid):
            raise HTTPException(404, "no such conversation")
        turns = store.turns(cid)
        return {
            "conversation_id": cid,
            "turns": [{k: v for k, v in t.items() if k != "messages"} for t in turns],
        }

    @app.post("/api/conversations/{cid}/ask")
    async def ask(cid: str, body: AskBody) -> StreamingResponse:
        if not store.exists(cid):
            raise HTTPException(404, "no such conversation")
        lock = locks.setdefault(cid, asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "a run is already in flight for this conversation")

        async def stream():
            async with lock:
                con = db()
                retrieved, seen = store.gate_sets(cid)
                deps = Deps(db=con, retrieved=retrieved, seen_entities=seen)
                history = store.history(cid)
                context_turns = len(store.turns(cid))
                try:
                    async for ev in answer_question(deps, body.question, history, model=state["model"]):
                        if ev["event"] == "answer":
                            aid = store.save_answer(cid, body.question, ev)
                            store.append_turn(
                                cid,
                                {
                                    "question": body.question,
                                    "answer_id": aid,
                                    "retrieved": sorted(deps.retrieved),
                                    "seen_entities": sorted(deps.seen_entities),
                                    "messages": store.serialize_messages(ev["new_messages"]),
                                },
                            )
                            payload = {k: v for k, v in ev.items() if k != "new_messages"}
                            payload["answer_id"] = aid
                            payload["context_turns"] = context_turns
                            payload["coverage"] = read_meta(con).get("funnel")
                            yield _sse("answer", payload)
                        else:
                            yield _sse(ev["event"], ev)
                finally:
                    con.close()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---- answers (permalinks)
    @app.get("/api/answers/{aid}")
    def get_answer(aid: str) -> dict[str, Any]:
        rec = store.load_answer(aid)
        if rec is None:
            raise HTTPException(404, "no such answer")
        return rec

    # ---- trials / entities / sql
    @app.get("/api/trials/{nct_id}")
    def trial(nct_id: str) -> dict[str, Any]:
        con = db()
        try:
            try:
                card = T.get_trial(con, nct_id)
            except T.SqlRejected as e:
                raise HTTPException(400, str(e)) from e
            if card is None:
                raise HTTPException(404, "not in the index")
            return card
        finally:
            con.close()

    @app.get("/api/entities/resolve")
    def resolve(q: str = Query(min_length=1, max_length=200), kind: T.Kind = "auto") -> dict[str, Any]:
        con = db()
        try:
            return T.resolve(con, q, kind).model_dump()
        finally:
            con.close()

    @app.post("/api/sql")
    def sql(body: SqlBody) -> dict[str, Any]:
        con = db()
        try:
            try:
                res = T.sandboxed_query(con, body.sql)
            except T.SqlRejected as e:
                raise HTTPException(400, str(e)) from e
            out = res.for_model()
            out["sql"] = res.sql
            return out
        finally:
            con.close()

    # ---- static frontend
    if WEB_DIR.exists():

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
    return app


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
