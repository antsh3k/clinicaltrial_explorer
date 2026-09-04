"""Typed population lexicon matcher (spec §5.5, App. B.8): biomarkers + patient subgroups.

Word-boundary regexes over title, listed conditions, and eligibility text; the matched LINE is stored as
`evidence_line`. Inclusion-vs-exclusion is NOT parsed in v1 (labeled recall-limited; the agent verifies
top hits by reading eligibility via get_trial).

Performance: each entry carries `triggers` — every alphanumeric literal run (≥2 chars) that appears in
its patterns, lowercased. A regex can only match text that contains at least one of those runs, so the
regexes run only when a cheap substring test fires. Entries with no such run are always tested.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass

from ct_landscape.normalize.lexicons import load

KINDS = ("biomarker", "demographic", "disease_severity", "prior_therapy", "line_of_therapy", "disease_stage")
_ALNUM_RUN = re.compile(r"[A-Za-z0-9]{2,}")
_ESCAPES = re.compile(r"\\[bBdDwWsSAZ]")  # regex escapes whose letters are NOT literals


@dataclass(frozen=True)
class Entry:
    term_id: str
    kind: str
    label: str
    patterns: tuple[re.Pattern, ...]
    triggers: tuple[str, ...]


def _triggers(patterns: list[str]) -> tuple[str, ...]:
    runs: set[str] = set()
    for p in patterns:
        stripped = _ESCAPES.sub(" ", p)
        for m in _ALNUM_RUN.finditer(stripped):
            runs.add(m.group(0).lower())
    return tuple(sorted(runs, key=len, reverse=True))


@functools.cache
def entries() -> tuple[Entry, ...]:
    out = []
    for e in load("populations")["entries"]:
        assert e["kind"] in KINDS, e
        out.append(
            Entry(
                e["term_id"],
                e["kind"],
                e["label"],
                tuple(re.compile(p, re.IGNORECASE) for p in e["patterns"]),
                _triggers(list(e["patterns"])),
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class Mention:
    term_id: str
    kind: str
    surface: str  # 'title' | 'condition' | 'eligibility'
    evidence_line: str


def _line_at(text: str, pos: int, max_len: int = 300) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end == -1:
        end = len(text)
    return text[start:end].strip()[:max_len]


def find_mentions(title: str | None, conditions: list[str], eligibility: str | None) -> list[Mention]:
    """One Mention per (term_id, surface): the first matching line is the evidence."""
    found: list[Mention] = []
    surfaces = [
        ("title", title or ""),
        ("condition", "\n".join(conditions)),
        ("eligibility", eligibility or ""),
    ]
    for surface, text in surfaces:
        if not text:
            continue
        low = text.lower()
        for e in entries():
            if e.triggers and not any(t in low for t in e.triggers):
                continue
            for rx in e.patterns:
                m = rx.search(text)
                if m:
                    found.append(Mention(e.term_id, e.kind, surface, _line_at(text, m.start())))
                    break
    return found
