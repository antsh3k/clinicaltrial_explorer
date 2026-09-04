"""Gold-set loader (spec §8.3): Pydantic with extra="forbid" — a YAML typo fails at the boundary naming the field."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

GOLD_PATH = Path(__file__).resolve().parent / "gold.yaml"

CheckKind = Literal[
    "entity_set",  # expected.entities vs answer table/entities → pooled entity P/R/F1
    "nct_set",  # expected.ncts vs cited/table NCTs → pooled NCT P/R
    "contains_all",  # every expected.entities item appears in the answer (table/entities/prose, alias-tolerant)
    "top_k_contains",  # expected.entities ⊆ first k table rows
    "honest_empty",  # answer must assert absence (no entities, no citations, an explicit caveat/absence phrase)
    "refuse_approval",  # answer must not claim approval; must carry the phase≠approval caveat
    "role_split",  # answer must mention both subject and comparator roles with counts
    "reconcile",  # two numbers in the answer must agree with SQL run by the harness
    "states_rollup",  # answer must name the condition surfaces (listed / mesh_leaf / ancestor) it used
]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Expected(_M):
    entities: list[
        str
    ] = []  # canonical asset ids / names / symbols; matched alias-tolerantly (lowercase, alnum)
    ncts: list[str] = []
    k: int | None = None  # for top_k_contains
    must_mention: list[str] = []  # substrings (case-insensitive) the answer_md must contain
    must_not_mention: list[str] = []
    condition_key: str | None = None  # for reconcile / scope checks
    asset_id: str | None = None


class GoldCase(_M):
    id: str
    archetype: Literal["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "negative", "messiness"]
    question: str
    check: CheckKind
    expected: Expected = Field(default_factory=Expected)
    oracle_url: str | None = None
    capture_date: str | None = None
    raw_ui_count: int | None = None
    adjudicated: bool = (
        False  # expected sets frozen against the oracle; unadjudicated set cases are DIAG only
    )
    adjudicated_by: str | None = None  # who/what froze the set and how (reviewer, date, oracle method)
    borderline: bool = False  # reported, excluded from gates
    note: str = ""


class Thresholds(_M):
    nct_precision: float = 0.80
    nct_recall: float = 0.70
    entity_f1: float = 0.75
    min_pooled_gold_items: int = 30  # set-based OBJ metrics become thresholds only above this pooled count


class Metadata(_M):
    source: str
    as_of: str
    thresholds: Thresholds = Field(default_factory=Thresholds)
    n_cases: int


class Gold(_M):
    metadata: Metadata
    cases: list[GoldCase]


def load_gold(path: Path = GOLD_PATH) -> Gold:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    g = Gold.model_validate(data)
    if g.metadata.n_cases != len(g.cases):
        raise ValueError(f"gold.yaml metadata.n_cases={g.metadata.n_cases} but {len(g.cases)} cases listed")
    ids = [c.id for c in g.cases]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case ids in gold.yaml")
    return g
