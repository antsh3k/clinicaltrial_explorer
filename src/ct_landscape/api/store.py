"""Filesystem answer + conversation store (spec §7.5): the DuckDB file stays read-only at serve time.

  runs/answers/{answer_id}.json          persisted answer + trace (the permalink; doubles as eval replay fixture)
  runs/conversations/{id}.jsonl          one line per turn: question, answer_id, serialized new model messages,
                                         and the conversation-scoped gate sets (retrieved / seen_entities)
History compaction: prior turns keep their model messages, but every tool-return payload is replaced by a digest
(the gate's sets live in the store, independent of what the model still sees); ~20-turn cap.
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelRequest, ToolReturnPart

RUNS = Path("runs")
MAX_TURNS = 20


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))


class Store:
    def __init__(self, root: Path = RUNS) -> None:
        self.root = root
        (root / "answers").mkdir(parents=True, exist_ok=True)
        (root / "conversations").mkdir(parents=True, exist_ok=True)

    # ---- conversations
    def new_conversation(self) -> str:
        cid = time.strftime("%Y%m%d") + "-" + secrets.token_hex(4)
        (self.root / "conversations" / f"{cid}.jsonl").touch()
        return cid

    def conversation_path(self, cid: str) -> Path:
        if not cid.replace("-", "").isalnum():
            raise ValueError("bad conversation id")
        return self.root / "conversations" / f"{cid}.jsonl"

    def exists(self, cid: str) -> bool:
        return self.conversation_path(cid).exists()

    def turns(self, cid: str) -> list[dict[str, Any]]:
        p = self.conversation_path(cid)
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    def append_turn(self, cid: str, turn: dict[str, Any]) -> None:
        with self.conversation_path(cid).open("a") as f:
            f.write(json.dumps(_jsonable(turn)) + "\n")

    def gate_sets(self, cid: str) -> tuple[set[str], set[str]]:
        retrieved: set[str] = set()
        seen: set[str] = set()
        for t in self.turns(cid):
            retrieved.update(t.get("retrieved", []))
            seen.update(t.get("seen_entities", []))
        return retrieved, seen

    def history(self, cid: str) -> list[ModelMessage]:
        """Model messages of prior turns with tool payloads digested; capped at MAX_TURNS most recent turns."""
        msgs: list[ModelMessage] = []
        for t in self.turns(cid)[-MAX_TURNS:]:
            raw = t.get("messages")
            if not raw:
                continue
            turn_msgs = ModelMessagesTypeAdapter.validate_python(raw)
            msgs.extend(_digest(turn_msgs))
        return msgs

    # ---- answers
    def save_answer(self, cid: str, question: str, event: dict[str, Any]) -> str:
        aid = "a" + secrets.token_hex(4)
        record = {
            "answer_id": aid,
            "conversation_id": cid,
            "question": question,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "answer": event["answer"],
            "gate": event.get("gate"),
            "trace": event.get("trace", []),
            "retrieved": event.get("retrieved", []),
            "usage": event.get("usage"),
            "elapsed_ms": event.get("elapsed_ms"),
            "context_turns": len(self.turns(cid)),
        }
        (self.root / "answers" / f"{aid}.json").write_text(json.dumps(_jsonable(record), indent=1))
        return aid

    def load_answer(self, aid: str) -> dict[str, Any] | None:
        if not aid.isalnum():
            return None
        p = self.root / "answers" / f"{aid}.json"
        return json.loads(p.read_text()) if p.exists() else None

    def serialize_messages(self, messages: list[ModelMessage]) -> Any:
        return ModelMessagesTypeAdapter.dump_python(messages, mode="json")


def _digest(messages: list[ModelMessage]) -> list[ModelMessage]:
    out: list[ModelMessage] = []
    for m in messages:
        if isinstance(m, ModelRequest):
            parts = []
            for p in m.parts:
                if isinstance(p, ToolReturnPart):
                    content = p.content
                    summary: str
                    if isinstance(content, dict) and "result" in content:
                        r = content["result"]
                        if isinstance(r, dict) and "total_row_count" in r:
                            summary = f"[digest] {p.tool_name} → {r['total_row_count']} rows; columns {r.get('columns')}"
                        elif isinstance(r, dict) and "candidates" in r:
                            summary = f"[digest] {p.tool_name} → " + ", ".join(
                                f"{c['kind']}:{c['id']}" for c in r["candidates"][:5]
                            )
                        else:
                            summary = (
                                f"[digest] {p.tool_name} → (result elided from context; ids remain citable)"
                            )
                    else:
                        summary = f"[digest] {p.tool_name} → {str(content)[:200]}"
                    parts.append(
                        ToolReturnPart(
                            tool_name=p.tool_name,
                            content=summary,
                            tool_call_id=p.tool_call_id,
                            timestamp=p.timestamp,
                        )
                    )
                else:
                    parts.append(p)
            out.append(ModelRequest(parts=parts, instructions=m.instructions))
        else:
            out.append(m)
    return out
