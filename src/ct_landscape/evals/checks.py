"""Eval core (spec §8.2, App. B.10): two axes, one small core.

Every check emits a CheckResult{metric, value, role, section, detail[], denominator}. Role is how the number may
be used: FLOOR (a defect count; any breach fails the run — an optimizer can never trade a FLOOR regression for an
OBJ gain), OBJ (the quality score, averaged), DIAG (recorded, structurally inert). Set metrics are POOLED
(Σ numerators / Σ denominators) — never a macro mean over per-case rates, which weights a 1-item gold like a
40-item one and rewards empty gold sets.
"""

from __future__ import annotations

from enum import StrEnum
from statistics import mean

from pydantic import BaseModel


class Role(StrEnum):
    OBJ = "OBJ"
    FLOOR = "FLOOR"
    DIAG = "DIAG"


class CheckResult(BaseModel):
    metric: str
    value: float
    role: Role
    section: str
    detail: list[dict] = []  # the offending items, for triage
    denominator: float | None = None  # evaluable population behind a rate/count


class ObjectiveResult(BaseModel):
    obj_score: float
    floor_breaches: list[str]
    passed: bool


def roll_up(results: list[CheckResult], floor_thresholds: dict[str, float] | None = None) -> ObjectiveResult:
    thr = floor_thresholds or {}
    breaches = [r.metric for r in results if r.role is Role.FLOOR and r.value > thr.get(r.metric, 0.0)]
    objs = [r.value for r in results if r.role is Role.OBJ]
    return ObjectiveResult(
        obj_score=mean(objs) if objs else 0.0, floor_breaches=breaches, passed=not breaches
    )


def set_prf(returned: frozenset[str], gold: frozenset[str]) -> tuple[float, float, float]:
    """Closed-world P/R/F1 over opaque ids, with the pinned edge cases from §8.2:
    empty gold + empty returned → 1/1/1; empty gold + non-empty → R=1, P=0; non-empty gold + empty → R=0, P=1."""
    if not gold and not returned:
        return 1.0, 1.0, 1.0
    tp = len(returned & gold)
    p = tp / len(returned) if returned else 1.0
    r = tp / len(gold) if gold else 1.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


class Pooled:
    """Accumulate tp / |returned| / |gold| across cases; report pooled P/R/F1 with their denominators."""

    def __init__(self) -> None:
        self.tp = 0
        self.n_returned = 0
        self.n_gold = 0
        self.cases: list[str] = []

    def add(self, case_id: str, returned: frozenset[str], gold: frozenset[str]) -> None:
        self.cases.append(case_id)
        self.tp += len(returned & gold)
        self.n_returned += len(returned)
        self.n_gold += len(gold)

    def precision(self) -> float:
        return self.tp / self.n_returned if self.n_returned else 1.0

    def recall(self) -> float:
        return self.tp / self.n_gold if self.n_gold else 1.0

    def f1(self) -> float:
        p, r = self.precision(), self.recall()
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def results(self, prefix: str, section: str) -> list[CheckResult]:
        return [
            CheckResult(
                metric=f"{prefix}_precision",
                value=round(self.precision(), 4),
                role=Role.OBJ,
                section=section,
                denominator=self.n_returned,
                detail=[{"cases": self.cases, "tp": self.tp}],
            ),
            CheckResult(
                metric=f"{prefix}_recall",
                value=round(self.recall(), 4),
                role=Role.OBJ,
                section=section,
                denominator=self.n_gold,
                detail=[{"cases": self.cases, "tp": self.tp}],
            ),
            CheckResult(
                metric=f"{prefix}_f1",
                value=round(self.f1(), 4),
                role=Role.DIAG,
                section=section,
                denominator=self.n_gold,
            ),
        ]
