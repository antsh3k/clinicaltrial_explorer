"""Phase normalization — single-sourced (spec §5 Stage 1, App. B.1).

`phases[]` → `phase_norm` (max token under the round-UP rule) and `phase_rank` (ordinal).

Rules:
- combined phases round UP: ["PHASE2","PHASE3"] → PHASE3; ["PHASE1","PHASE2"] → PHASE2
- [] / absent → None. On OBSERVATIONAL / EXPANDED_ACCESS studies this is a different study kind,
  not "unknown phase" — callers decide what to do with study_type, not this module.
- NA stays NA (behavioral/device); its rank is None.
- unmapped tokens → ignored; if nothing maps, None (never a default).
- whitelist checked by set intersection over tokens, never by string-joining the list.
"""

from __future__ import annotations

PHASE_RANK: dict[str, float] = {
    "EARLY_PHASE1": 0.5,
    "PHASE1": 1.0,
    "PHASE2": 2.0,
    "PHASE3": 3.0,
    "PHASE4": 4.0,
}
PHASE_TOKENS: frozenset[str] = frozenset(PHASE_RANK) | {"NA"}


def phase_norm(phases: list[str] | None) -> str | None:
    """Max phase token under round-up; 'NA' if that is the only recognised token; None if none."""
    if not phases:
        return None
    tokens = {p.strip().upper() for p in phases if isinstance(p, str)}
    ranked = tokens & PHASE_RANK.keys()
    if ranked:
        return max(ranked, key=PHASE_RANK.__getitem__)
    if "NA" in tokens:
        return "NA"
    return None


def phase_rank(norm: str | None) -> float | None:
    """Ordinal for a normalized phase; NA and None → None."""
    return PHASE_RANK.get(norm) if norm else None
