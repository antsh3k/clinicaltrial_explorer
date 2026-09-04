"""Offline replay models (spec §8.2 replay_mismatch_count, §11.1 Phase 4 definition of done).

`scripted_model(fn)` wraps a plain `(messages, info) -> ModelResponse` function into a FunctionModel that ALSO
serves the streamed request path the production agent uses (`run_stream_events`), so recorded transcripts and
hand-written scripts drive the real agent, the real tools and the real output validator with no network.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

ScriptFn = Callable[[list[ModelMessage], AgentInfo], ModelResponse]


def scripted_model(fn: ScriptFn, model_name: str = "scripted") -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        resp = fn(messages, info)
        emitted = False
        for i, part in enumerate(resp.parts):
            if isinstance(part, TextPart):
                yield part.content
                emitted = True
            elif isinstance(part, ToolCallPart):
                args = part.args if isinstance(part.args, str) else json.dumps(part.args)
                yield {i: DeltaToolCall(name=part.tool_name, json_args=args, tool_call_id=part.tool_call_id)}
                emitted = True
        if not emitted:
            yield ""

    return FunctionModel(fn, stream_function=stream, model_name=model_name)


def transcript_model(turns: list[ModelResponse]) -> FunctionModel:
    """Replay a RECORDED transcript verbatim: the n-th model request gets the n-th recorded response.
    Running out of turns raises — a transcript that no longer fits the tools is a replay mismatch."""
    state = {"i": 0}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        i = state["i"]
        if i >= len(turns):
            raise RuntimeError(
                f"replay mismatch: transcript has {len(turns)} model turns, agent asked for turn {i + 1}"
            )
        state["i"] += 1
        return turns[i]

    return scripted_model(fn, model_name="transcript")
