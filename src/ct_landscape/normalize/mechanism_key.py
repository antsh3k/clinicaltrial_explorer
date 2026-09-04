"""Mechanism-key fold (spec §4.3, App. B.7): 'anti-CD20 antibody' and 'CD20 inhibitor' meet at 'cd20'.

- casefold; strip a leading `anti` ONLY when followed by a dash/whitespace (so 'antithrombin' survives)
- split on [\\s,;/|+&]+; drop modality stopwords; expand numeric-suffix shorthand (JAK1/2 → jak1, jak2)
- key = "|".join(sorted(tokens)) — a deterministic scalar, never a serialized set
"""

from __future__ import annotations

import re

_ANTI = re.compile(r"\banti[-‐‑‒–—―\s]+", re.IGNORECASE)
_SPLIT = re.compile(r"[\s,;/|+&]+")
_STOP = {
    "inhibitor",
    "inhibitors",
    "inhibition",
    "antagonist",
    "antagonists",
    "agonist",
    "agonists",
    "modulator",
    "modulators",
    "blocker",
    "blockers",
    "blockade",
    "activator",
    "activators",
    "activation",
    "receptor",
    "receptors",
    "ligand",
    "ligands",
    "pathway",
    "signaling",
    "signalling",
    "antibody",
    "antibodies",
    "monoclonal",
    "mab",
    "anti",
    "the",
    "of",
    "and",
    "a",
    "an",
    "targeted",
    "targeting",
    "directed",
    "against",
    "selective",
    "small",
    "molecule",
    "oral",
    "human",
    "humanized",
    "humanised",
    "bispecific",
    "conjugate",
    "drug",
    "agent",
    "agents",
    "therapy",
    "class",
    "based",
    "specific",
    "dual",
    "pan",
    "type",
    "protein",
    "kinase",
    "kinases",
    "enzyme",
    "channel",
    "transporter",
    "degrader",
    "stimulator",
    "stabilizer",
    "stabiliser",
    "binding",
    "binder",
    "blocking",
    "neutralizing",
    "neutralising",
    "immune",
    "checkpoint",
    "cell",
    "cells",
    "engager",
    "t",
    "car",
    "vaccine",
    "gene",
    "rna",
    "sirna",
    "antisense",
    "oligonucleotide",
    "aso",
}
_ALPHA_PREFIX = re.compile(r"^([a-z]+)\d+[a-z]?$")
_BARE_NUMBER = re.compile(r"^\d+[a-z]?$")
_PUNCT_EDGE = re.compile(r"^[^a-z0-9]+|[^a-z0-9]+$")


def mechanism_tokens(text: str) -> list[str]:
    s = _ANTI.sub("", (text or "").casefold())
    raw = [t for t in _SPLIT.split(s) if t]
    out: list[str] = []
    prev_alpha: str | None = None
    for t in raw:
        t = _PUNCT_EDGE.sub("", t)
        if not t:
            continue
        if _BARE_NUMBER.match(t) and prev_alpha:
            t = prev_alpha + t  # 'jak1/2' → 'jak1', '2' → 'jak2'
        m = _ALPHA_PREFIX.match(t)
        prev_alpha = m.group(1) if m else None
        if t in _STOP:
            continue
        out.append(t)
    return out


def mechanism_key(text: str) -> str:
    """Deterministic scalar key; empty string when nothing survives the fold."""
    return "|".join(sorted(set(mechanism_tokens(text))))
