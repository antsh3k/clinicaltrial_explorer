"""Sponsor name → company key (spec §5.4, App. B.6).

Suffix-pop normalizer (lowercase; [.,&-] → space; collapse; pop trailing legal-form / industry tokens in a
loop, longest match first) + a curated alias file for name groups that share no token + a small set of
regexes for sponsor strings that DECLARE their own parent ("X, a Sanofi Company"). Never substring
containment as equality.
"""

from __future__ import annotations

import functools
import re
import unicodedata

from ct_landscape.normalize.lexicons import load

_PUNCT = re.compile(r"[.,&\-–—/()'\"’‘:;+]+")
_WS = re.compile(r"\s+")
_TRAILING_PAREN = re.compile(r"\s*\([^()]*\)\s*$")


@functools.cache
def _lex():
    suf = load("company_suffixes")
    ali = load("company_aliases")
    tokens = sorted(set(suf["legal_forms"]) | set(suf["industry_words"]), key=lambda t: -len(t.split()))
    alias_map: dict[str, str] = {}
    canon: dict[str, str] = {}
    for g in ali["groups"]:
        cid = _basic_norm(g["canonical"])
        canon[cid] = g["canonical"]
        for m in g["members"]:
            alias_map[_basic_norm(m)] = cid
        alias_map[cid] = cid
    parents = [re.compile(p, re.IGNORECASE) for p in suf["declared_parent_patterns"]]
    return {"suffix_tokens": tokens, "alias_map": alias_map, "canonical": canon, "parents": parents}


def _basic_norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def declared_parent(raw: str) -> str | None:
    """'Genzyme, a Sanofi Company' → 'Sanofi'; 'Hospira, now a wholly owned subsidiary of Pfizer' → 'Pfizer'."""
    s = _TRAILING_PAREN.sub("", raw or "").strip()
    for rx in _lex()["parents"]:
        m = rx.match(s)
        if m:
            parent = m.group(2).strip()
            parent = _TRAILING_PAREN.sub("", parent)
            if parent:
                return parent
    return None


def pop_suffixes(norm: str) -> str:
    """Pop trailing legal-form/industry tokens in a loop; never pop the last remaining token."""
    toks = norm.split()
    changed = True
    while changed and len(toks) > 1:
        changed = False
        for suffix in _lex()["suffix_tokens"]:
            st = suffix.split()
            n = len(st)
            if len(toks) > n and toks[-n:] == st:
                toks = toks[:-n]
                changed = True
                break
    return " ".join(toks)


def company_key(raw: str) -> str:
    """The normalized key: declared parent (if any) → strip trailing parenthetical → basic norm → suffix pop →
    curated alias map. Empty input → 'unknown'."""
    if not raw or not raw.strip():
        return "unknown"
    parent = declared_parent(raw)
    base = parent if parent else _TRAILING_PAREN.sub("", raw)
    norm = pop_suffixes(_basic_norm(base))
    if not norm:
        norm = _basic_norm(base) or "unknown"
    return _lex()["alias_map"].get(norm, norm)


def canonical_display(key: str, most_common_raw: str) -> str:
    """Curated groups display their canonical label; everything else the most frequent raw surface."""
    return _lex()["canonical"].get(key, most_common_raw)
