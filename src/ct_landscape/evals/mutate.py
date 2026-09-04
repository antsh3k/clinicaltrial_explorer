"""Mutation mini-suite operators (spec §8.4): take one known-good answer, plant exactly one defect.

Operators are pure (deep-copy in, one planted defect out) and select their target by PRECONDITION, never by a
hard-coded index; a seed that cannot host the mutation raises — a malformed seed must fail loud, never silently
plant nothing. tests/test_gate.py asserts each operator breaks exactly its FLOOR and that the control has 0 findings.
"""

from __future__ import annotations

import copy
from collections.abc import Callable

from ct_landscape.agent.gate import Answer, Citation, EntityRef

Operator = Callable[[Answer], Answer]


def fabricated_nct(a: Answer) -> Answer:
    a = copy.deepcopy(a)
    if "NCT09999999" in a.answer_md:
        raise ValueError("seed already contains the planted id")
    a.answer_md += " Also see NCT09999999."
    return a


def seven_digit_nct(a: Answer) -> Answer:
    a = copy.deepcopy(a)
    a.answer_md += " And NCT1234567."
    return a


def citation_outside_retrieved(a: Answer) -> Answer:
    a = copy.deepcopy(a)
    if not a.citations:
        raise ValueError("seed must carry a citation to mutate")
    a.citations[0] = Citation(nct_id="NCT00000001", why="planted")
    return a


def entity_never_returned(a: Answer) -> Answer:
    a = copy.deepcopy(a)
    if not a.entities:
        raise ValueError("seed must carry an entity to mutate")
    a.entities.append(EntityRef(kind="drug", id="zzz-planted-entity"))
    return a


def table_cell_nct(a: Answer) -> Answer:
    a = copy.deepcopy(a)
    if a.table is None:
        raise ValueError("seed must carry a table to mutate")
    a.table.rows.append(["planted", "NCT08888888"])
    return a


OPERATORS: dict[str, tuple[Operator, str]] = {  # name → (operator, the FLOOR it must break)
    "fabricated_nct": (fabricated_nct, "NCT never retrieved: NCT09999999"),
    "seven_digit_nct": (seven_digit_nct, "malformed NCT: NCT1234567"),
    "citation_outside_retrieved": (citation_outside_retrieved, "fabricated citation: NCT00000001"),
    "entity_never_returned": (
        entity_never_returned,
        "entity never in a tool result: drug:zzz-planted-entity",
    ),
    "table_cell_nct": (table_cell_nct, "NCT never retrieved: NCT08888888"),
}
