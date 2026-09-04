"""Arm roles + background-therapy signal (spec §5.2).

Per (trial, asset): role = 'subject' if the asset appears in ≥1 EXPERIMENTAL arm; 'comparator' if it appears
ONLY in ACTIVE_COMPARATOR / PLACEBO_COMPARATOR / SHAM_COMPARATOR / NO_INTERVENTION arms; else 'unknown'
(OTHER-typed arms, NULL arm types, arm-less records). Subject-first; OTHER belongs to NEITHER set.
`in_all_arms` = asset present in every arm — defined only when the trial has ≥2 arms, NULL otherwise.
"""

from __future__ import annotations

from collections import defaultdict

SUBJECT_ARMS = {"EXPERIMENTAL"}
COMPARATOR_ARMS = {"ACTIVE_COMPARATOR", "PLACEBO_COMPARATOR", "SHAM_COMPARATOR", "NO_INTERVENTION"}


def role_for(arm_types: list[str | None]) -> str:
    types = {t for t in arm_types if t}
    if types & SUBJECT_ARMS:
        return "subject"
    if types and types <= COMPARATOR_ARMS:
        return "comparator"
    return "unknown"


def assign_roles(
    intervention_assets: dict[tuple[str, int], list[tuple[str, str]]],
    arm_links: dict[tuple[str, int], list[int]],  # (nct, intervention_no) → arm_nos
    arm_types: dict[tuple[str, int], str | None],  # (nct, arm_no) → type
    n_arms: dict[str, int],
) -> list[tuple[str, int, str, str, str, bool | None]]:
    """→ rows (nct_id, intervention_no, asset_id, via, role, in_all_arms). Role is per (trial, asset) — the
    same asset reached through two interventions gets one role computed over ALL its arms."""
    # collect arms per (nct, asset)
    arms_by_ta: dict[tuple[str, str], set[int]] = defaultdict(set)
    rows_src: list[tuple[str, int, str, str]] = []
    for (nct, no), assets in intervention_assets.items():
        arms = arm_links.get((nct, no), [])
        for aid, via in assets:
            arms_by_ta[(nct, aid)].update(arms)
            rows_src.append((nct, no, aid, via))
    role_cache: dict[tuple[str, str], tuple[str, bool | None]] = {}
    out = []
    seen: set[tuple[str, int, str]] = set()
    for nct, no, aid, via in rows_src:
        if (nct, no, aid) in seen:
            continue
        seen.add((nct, no, aid))
        key = (nct, aid)
        if key not in role_cache:
            arm_nos = arms_by_ta[key]
            role = role_for([arm_types.get((nct, a)) for a in arm_nos])
            total = n_arms.get(nct, 0)
            in_all = (len(arm_nos) == total) if total >= 2 else None
            role_cache[key] = (role, in_all)
        role, in_all = role_cache[key]
        out.append((nct, no, aid, via, role, in_all))
    return out
