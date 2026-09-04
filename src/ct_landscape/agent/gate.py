"""Fail-closed grounding gate + the structured Answer contract (spec §7.4, App. B.9).

Our evidence is a local index we fully observe, so a cited-but-never-retrieved NCT is a hard defect
(fabrication), not an uncertainty. The gate is a PURE function over (answer, retrieved, seen_entities);
agent.py wires it as the output validator so it runs on every final answer, after every tool call.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NCT_CANDIDATE = re.compile(r"NCT[\s\-]?(\d+)", re.IGNORECASE)
EntityKind = Literal["drug", "condition", "company", "moa", "population"]


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nct_id: str = Field(description="NCT id copied from a query result")
    why: str = Field(description="What this trial supports in the answer")


class EntityRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: EntityKind
    id: str = Field(
        description="Entity id exactly as returned by resolve_entity / run_sql (asset_id, condition_key, company_id, moa_key, term_id)"
    )


class Table(BaseModel):
    model_config = ConfigDict(extra="forbid")
    columns: list[str]
    rows: list[list[str | int | float | bool | None]]


class Answer(BaseModel):
    """The agent's only way to end a run (submitted via the `submit_answer` tool)."""

    model_config = ConfigDict(extra="forbid")
    answer_md: str = Field(
        description="The answer in markdown; every NCT mentioned must come from a tool result"
    )
    citations: list[Citation] = Field(default_factory=list, description="Per-claim NCT citations")
    entities: list[EntityRef] = Field(
        default_factory=list, description="Entities the answer is about (ids from tool results)"
    )
    table: Table | None = Field(
        default=None, description="Preferred: a ranked table; the UI renders it, the eval scores it"
    )
    caveats: list[str] = Field(default_factory=list)

    def table_text(self) -> str:
        if not self.table:
            return ""
        return "\n".join(" | ".join("" if c is None else str(c) for c in row) for row in self.table.rows)


def nct_refs_from_text(text: str) -> list[tuple[str, str, bool]]:
    """(canonical, raw, well_formed). A separator is TOLERATED for identity — the digit run still identifies
    the trial — but ONLY the digit count drives well-formedness (exactly 8), so a strict-zero defect metric
    never false-positives on a cosmetic dash."""
    return [
        (f"NCT{m.group(1)}", m.group(0), len(m.group(1)) == 8) for m in NCT_CANDIDATE.finditer(text or "")
    ]


def gate(answer: Answer, retrieved: set[str], seen_entities: set[str]) -> list[str]:
    """Violations list; empty = pass. FAIL-CLOSED."""
    errs: list[str] = []
    text = answer.answer_md + "\n" + answer.table_text()
    seen_bad: set[str] = set()
    for canon, raw, wellformed in nct_refs_from_text(text):
        if not wellformed:
            if raw not in seen_bad:
                errs.append(f"malformed NCT: {raw}")
                seen_bad.add(raw)
        elif canon not in retrieved and canon not in seen_bad:
            errs.append(f"NCT never retrieved: {canon}")
            seen_bad.add(canon)
    for c in answer.citations:
        if c.nct_id not in retrieved:
            errs.append(f"fabricated citation: {c.nct_id}")
    for e in answer.entities:  # GROUNDED, not merely existing
        if e.id not in seen_entities:
            errs.append(f"entity never in a tool result: {e.kind}:{e.id}")
    return errs
