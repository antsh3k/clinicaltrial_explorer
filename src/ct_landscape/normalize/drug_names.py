"""Intervention name → cleaning → whole-label noise gates → dedup-key router (spec §5.1, App. B.2–B.3).

Pure functions, no I/O beyond loading the YAML lexicons once. The router picks one of three keys:
  (i)   combination names → sorted component keys joined with "+"
  (ii)  biologic-shaped names → isoform-preserving key (Greek qualifier / biosimilar suffix kept)
  (iii) everything else → fixed-point loop alternating salt and dose-form strips, with an electrolyte guard
Final keys are lowercase alphanumerics only ("+" separates combo components). No fuzzy matching, ever.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass, field

from ct_landscape.normalize.lexicons import load

# ---------------------------------------------------------------- lexicon-driven regexes


def _alt(words: list[str]) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


@functools.cache
def _lex():
    noise = load("noise_names")
    nonmol = load("non_molecule")
    salts = load("salt_dose_suffixes")
    quals = load("qualifiers")
    qual_alt = _alt(quals["leading"])
    return {
        "qual_lead": re.compile(r"^(?:" + qual_alt + r")(?:\s+|$)"),
        "qual_trail": re.compile(r"(?:^|\s+)(?:" + qual_alt + r")$"),
        "qual_lead_ci": re.compile(r"^(?:" + qual_alt + r")(?:\s+|$)", re.IGNORECASE),
        "qual_trail_ci": re.compile(r"(?:^|\s+)(?:" + qual_alt + r")$", re.IGNORECASE),
        "exact": set(noise["exact"]) | set(nonmol["class_labels"]),
        "starts": re.compile(r"^(?:" + _alt(noise["starts_with"]) + r")(?:\b|$)"),
        "contains": [c for c in noise["contains"]],
        "regex": [re.compile(r"^(?:" + r + r")$") for r in noise["regex"]],
        "salt": re.compile(r"\s+(?:" + _alt(salts["salts"]) + r")$"),
        "dose_form": re.compile(r"\s+(?:" + _alt(salts["dose_forms"]) + r")(?:\s.*)?$"),
        "device": re.compile(r"(?:\s+(?:" + _alt(salts["device_pack"]) + r"))+\s*$"),
        "cations": set(salts["electrolyte_cations"]),
        # tokens that are never a molecule on their own: a regimen split ("lenalidomide dexamethasone") must not
        # produce them as members ("abiraterone acetate" is one drug, "nicotine gum" is one drug)
        "form_words": {
            _NONALNUM_TOK.sub("", w.lower())
            for w in salts["salts"]
            + salts["dose_forms"]
            + salts["device_pack"]
            + salts["electrolyte_cations"]
            + quals["leading"]
        },
    }


_NONALNUM_TOK = re.compile(r"[^a-z0-9]")


def non_molecule_tokens() -> frozenset[str]:
    """Single tokens that can never stand for a molecule (salt, dose form, device, cation, qualifier words)."""
    return frozenset(_lex()["form_words"])


# ---------------------------------------------------------------- cleaning

_TYPE_PREFIX = re.compile(
    r"^\s*(?:drug|biological|biologic|genetic|combination product|dietary supplement|device|procedure|other|"
    r"experimental|active comparator|placebo comparator|sham comparator|comparator|investigational|control|"
    r"treatment|active arm|active|arm|group|cohort|intervention|test|reference)"
    r"(?:\s+(?:arm|group|drug|product))?\s*[:\-–—]\s*",
    re.IGNORECASE,
)
_ARM_LETTER_PREFIX = re.compile(
    r"^\s*(?:arm|group|cohort|part|stage)\s+[a-z0-9]{1,2}\s*[:\-–—]\s*", re.IGNORECASE
)
_PLACEBO_CLAUSE = re.compile(
    r"\s*(?:\bor\b|\band\b|\+|/|,)\s*(?:its\s+|corresponding\s+|matching\s+|matched\s+)?(?:placebo|sham|vehicle)s?\b.*$",
    re.IGNORECASE,
)
_DOSE = re.compile(
    r"(?<![A-Za-z0-9\-])"  # a number glued to a letter/hyphen is part of a code name ("HRS-5635 Injection" keeps 5635)
    r"(?:\d+(?:[.,]\d+)?\s*(?:-|to|–|or|/)\s*)?"  # range start "25-50 mg", "25 or 50 mg"
    r"\d+(?:[.,]\d+)?\s*(?:x\s*\d+\s*)?"
    r"(?:mg|mcg|µg|ug|g|kg|ml|mL|l|iu|units?|u|mmol|µmol|umol|meq|mci|mbq|gbq|gy|ppm|%|ng|pg|mg/kg|mg/m2|mg/m²|cells?/kg|"
    r"cells|copies|vg/kg|vg|pfu|tcid50|ccid50|cfu|spores|dose|doses|tablets?|capsules?|puffs?|drops?|sprays?|vials?|patches?|cycles?|courses?|infusions?|injections?)"
    r"(?:\s*/\s*(?:kg|m2|m²|ml|mL|l|day|d|dose|h|hr|hour|week|wk|kg/day|kg/dose|min))*\b",  # "mg/m²/day": every per-unit tail
    re.IGNORECASE,
)
_PERCENT = re.compile(r"\d+(?:[.,]\d+)?\s*%(?:\s*(?:w/w|w/v|v/v))?", re.IGNORECASE)
_FREQ = re.compile(
    r"\b(?:bid|tid|qid|qd|qod|qw|q\d+[wdh]|q\d+\s*(?:weeks?|days?|hours?|h)|"
    r"once[- ]daily|twice[- ]daily|three times daily|once[- ]weekly|twice[- ]weekly|every\s+(?:\d+|other)\s+(?:weeks?|days?|hours?|months?)|"
    r"daily|weekly|monthly|per day|per week|(?:for|x)\s+\d+\s+(?:days?|weeks?|months?|cycles?)|"
    r"day\s*\d+(?:\s*(?:-|to|–)\s*\d+)?|days?\s+\d+(?:\s*(?:-|to|–)\s*\d+)?|cycles?\s*\d+|week\s*\d+)\b",
    re.IGNORECASE,
)
_ROUTE_TAIL = re.compile(
    r"\b(?:administered|given|delivered|infused|injected)\s+(?:as\s+)?(?:an?\s+)?(?:intravenous(?:ly)?|iv|oral(?:ly)?|"
    r"subcutaneous(?:ly)?|sc|intramuscular(?:ly)?|im|topical(?:ly)?|infusion|injection|bolus)\b.*$",
    re.IGNORECASE,
)
_PAREN = re.compile(r"\s*[\(\[][^()\[\]]*[\)\]]")
_TRADEMARK = re.compile(r"[®™©]")
_OPEN_LABEL = re.compile(
    r"\b(?:open[- ]label|double[- ]blind|single[- ]blind|blinded|unblinded)\b", re.IGNORECASE
)
_TRAILING_JUNK = re.compile(r"[\s,;:\-–—/+&、，。]+$|^[\s,;:\-–—/+&、，。]+")
_DANGLING_CONJ = re.compile(
    r"\s+(?:or|and|with|plus|in combination with|combined with|followed by)\s*$", re.IGNORECASE
)
_COMBO_PHRASES = re.compile(
    r"\s+(?:in combination with|combined with|combination with|plus|together with|co-administered with)\s+",
    re.IGNORECASE,
)
_WS = re.compile(r"\s+")
_X_OR_X = re.compile(r"^(.+?)\s+(?:or|and|/)\s+\1$", re.IGNORECASE)


def clean(name: str, lower: bool = True) -> str:
    """Registry-specific cleaning (App. B.2). Returns a lowercase string (or case-preserved display form when
    lower=False); may be empty."""
    s = unicodedata.normalize("NFKC", name or "")  # fullwidth → ASCII
    s = _TRADEMARK.sub("", s)
    s = _TYPE_PREFIX.sub("", s)
    s = _ARM_LETTER_PREFIX.sub("", s)
    s = _PAREN.sub("", s)  # "(IL-17A inhibitor)", "(MK-3475)", "(placebo)" — the gate sees the raw form first
    s = _PLACEBO_CLAUSE.sub("", s)
    s = _OPEN_LABEL.sub(" ", s)
    s = _ROUTE_TAIL.sub("", s)
    s = _PERCENT.sub(" ", s)
    s = _DOSE.sub(" ", s)
    s = _FREQ.sub(" ", s)
    s = _COMBO_PHRASES.sub(" + ", s)
    if lower:
        s = s.lower()
    s = s.replace("’", "'").replace("‘", "'")
    s = _WS.sub(" ", s).strip()
    s = _DANGLING_CONJ.sub("", s)
    s = _TRAILING_JUNK.sub("", s).strip()
    m = _X_OR_X.match(s)
    if m:
        s = m.group(1)
    return s


_PLACEBO_REMNANT = re.compile(r"\b(?:placebo|placebos|sham|dummy)\b")


# ---------------------------------------------------------------- noise gates (whole-label)

_CODE_SHAPE = re.compile(r"^[A-Z]{1,5}[-\s]?\d{2,8}[A-Z]?$")
_CODE_SHAPE_LOOSE = re.compile(r"^[a-z]{1,5}[-\s]?\d{2,8}[a-z]?$")
_SHORT_NUMERIC = re.compile(r"^[\d\W]+$")


def gate(label: str) -> str | None:
    """Return the gate name that rejects this (lowercased, cleaned) label, or None if it survives.

    WHOLE-LABEL semantics: 'pembrolizumab immunotherapy' survives; 'immunotherapy' does not.
    """
    lx = _lex()
    s = label.strip().lower()
    if not s or len(s) <= 2:
        return "too_short"
    if len(s) > 80:
        return "too_long"
    if _SHORT_NUMERIC.match(s):
        return "no_letters"
    if s.endswith(":"):
        return "trailing_colon"
    if s in lx["exact"]:
        return "exact_noise"
    if lx["starts"].match(s):
        return "placebo_sham_prefix"
    for c in lx["contains"]:
        if c in s:
            return "metadata_cue"
    for rx in lx["regex"]:
        if rx.match(s):
            return "regex_noise"
    return None


def gate_raw(raw: str) -> str | None:
    """Gate the raw surface (lightly normalized) BEFORE parenthetical removal, so that
    'Nitrogen (placebo)' and 'Placebo (for pembrolizumab)' die whole."""
    s = unicodedata.normalize("NFKC", raw or "")
    s = _TRADEMARK.sub("", s)
    s = _TYPE_PREFIX.sub("", s)
    s = _WS.sub(" ", s).strip().lower()
    return gate(s)


def is_code_name(surface: str) -> bool:
    return bool(_CODE_SHAPE.match(surface.strip())) or bool(_CODE_SHAPE_LOOSE.match(surface.strip().lower()))


# ---------------------------------------------------------------- router

_COMBO_SPLIT = re.compile(r"\s*/\s*|\s*\+\s*|\s+and\s+|\s+with\s+|\s*&\s*")
_EITHER_OR = re.compile(r"\s+or\s+")
_GREEK = re.compile(r"\b(?:alfa|alpha|beta|gamma|delta|epsilon|zeta|lambda|theta|kappa)\b")
_BIO_STEM = re.compile(
    r"^[a-z]+(?:mab|cept|kin|kinra|tide|ase|gene|vec|cel|leucel|parvovec|cabtagene|ciloleucel)$"
)
_BIO_WORDS = re.compile(
    r"\b(?:antibody|antibodies|monoclonal|immunoglobulin|vaccine|toxin|toxoid|interferon|interleukin|"
    r"insulin|albumin|factor\s+[ivx]+|erythropoietin|epoetin|somatropin|filgrastim|heparin|enoxaparin)\b"
)
_PAYLOAD = re.compile(
    r"\b(?:pegol|vedotin|mafodotin|ravtansine|ozogamicin|emtansine|mertansine|tansine|sudotin|govitecan|deruxtecan|"
    r"axotin|tesirine|tirumotecan|ecteribulin|rezetecan|ciloleucel|axicabtagene|brexucabtagene|lisocabtagene|"
    r"idecabtagene|vicleucel|autoleucel|tecelra|aglutamer|bcma|pegylated|peg)\b"
)
_BIOSIMILAR = re.compile(r"^(.+[a-z])-([a-z]{4})$")
_NONALNUM = re.compile(r"[^a-z0-9]")


def is_biologic_shape(cleaned: str) -> bool:
    toks = cleaned.split()
    if not toks:
        return False
    if _GREEK.search(cleaned) or _BIO_WORDS.search(cleaned) or _PAYLOAD.search(cleaned):
        return True
    for tok in toks:  # a biologic stem anywhere ("neoadjuvant nivolumab"); a biosimilar suffix only ON a stem
        m = _BIOSIMILAR.match(tok)
        stem = m.group(1) if m else tok
        if _BIO_STEM.match(stem):
            return True
    return False


def strip_salt(s: str) -> str:
    lx = _lex()
    toks = s.split()
    if len(toks) == 2 and toks[0] in lx["cations"]:
        return s  # electrolyte guard: "potassium chloride" stays whole
    return lx["salt"].sub("", s)


def strip_dose_forms(s: str) -> str:
    return _lex()["dose_form"].sub("", s)


def strip_device(s: str) -> str:
    return _lex()["device"].sub("", s)


_NUMERIC_PREFIX = re.compile(r"^(?:\d+(?:st|nd|rd|th)?|[ivx]+)\s+", re.IGNORECASE)


def display_surface(raw: str) -> str:
    """Case-preserving display form: cleaned, then edge qualifiers peeled (case-insensitively)."""
    s = clean(raw, lower=False)
    lx = _lex()
    while True:
        n = lx["qual_lead_ci"].sub("", s).strip()
        n = _NUMERIC_PREFIX.sub("", n).strip()
        if not n:
            return s
        m = lx["qual_trail_ci"].sub("", n).strip()
        if not m:
            return n
        if m == s:
            return s
        s = m


def only_qualifiers(s: str) -> bool:
    """True when the whole label is qualifier words ("intravenous", "oral solution")."""
    lx = _lex()
    prev = None
    while s and s != prev:
        prev = s
        s = lx["qual_lead"].sub("", s).strip()
        s = _NUMERIC_PREFIX.sub("", s).strip()
    return not s


def strip_qualifiers(s: str) -> str:
    """Peel curated qualifier words from both EDGES until stable; never empties the name (caller gates that)."""
    lx = _lex()
    while True:
        n = lx["qual_lead"].sub("", s).strip()
        n = _NUMERIC_PREFIX.sub("", n).strip()
        if not n:
            return s
        m = lx["qual_trail"].sub("", n).strip()
        if not m:
            return n
        if m == s:
            return s
        s = m


def _key_single(cleaned: str) -> str:
    n = cleaned
    if is_biologic_shape(n):
        while True:  # keep Greek qualifier + biosimilar suffix; strip device/dose-form/qualifier edges
            m = strip_dose_forms(strip_device(strip_qualifiers(n)))
            if m == n:
                break
            n = m
    else:
        while True:
            m = strip_dose_forms(strip_salt(strip_device(strip_qualifiers(n))))
            if m == n:
                break
            n = m
    return _NONALNUM.sub("", n)


@dataclass
class Keyed:
    """Result of routing one raw intervention name."""

    raw: str
    cleaned: str
    key: str | None  # None ⇒ gated (see `gate_reason`)
    gate_reason: str | None = None
    components: list[str] = field(default_factory=list)  # component keys when is_combo
    component_surfaces: list[str] = field(default_factory=list)
    dropped_parts: list[tuple[str, str]] = field(default_factory=list)  # (part, gate) dropped from a combo
    is_combo: bool = False
    route: str = ""  # "gated" | "combo" | "biologic" | "fixed_point" | "either_or"


def _split_parts(cleaned: str) -> list[str]:
    parts = [p.strip() for p in _COMBO_SPLIT.split(cleaned)]
    return [p for p in parts if p]


def route(raw: str, known_tokens: frozenset[str] | None = None) -> Keyed:
    """The dedup-key router (App. B.2). One function, three key shapes, plus the gates in front.

    `known_tokens`: single-token keys of standalone assets already seen in the corpus. When given, a
    space-joined multi-token name whose EVERY token (after qualifier stripping) is a known standalone
    asset is a regimen ("lenalidomide dexamethasone") and routes as a combination. Deterministic,
    vocabulary-driven, never fuzzy."""
    g = gate_raw(raw)
    if g:
        return Keyed(raw=raw, cleaned="", key=None, gate_reason=g, route="gated")
    cleaned = clean(raw)
    g = gate(cleaned)
    if g:
        return Keyed(raw=raw, cleaned=cleaned, key=None, gate_reason=g, route="gated")
    if _PLACEBO_REMNANT.search(
        cleaned
    ):  # "fluoxetine placebo": a placebo OF the drug survived clause-stripping
        return Keyed(raw=raw, cleaned=cleaned, key=None, gate_reason="placebo_remnant", route="gated")

    # "A or B" arms: keep the single surviving molecule; two real molecules ⇒ an either/or arm, not an asset
    if _EITHER_OR.search(cleaned):
        alts = [p.strip() for p in _EITHER_OR.split(cleaned) if p.strip()]
        survivors = [a for a in alts if gate(a) is None]
        if len(survivors) == 1:
            cleaned = survivors[0]
        elif len(survivors) >= 2:
            return Keyed(raw=raw, cleaned=cleaned, key=None, gate_reason="either_or_arm", route="either_or")
        else:
            return Keyed(raw=raw, cleaned=cleaned, key=None, gate_reason="either_or_arm", route="either_or")

    parts = _split_parts(cleaned)
    if len(parts) >= 2:
        keep: list[tuple[str, str]] = []
        dropped: list[tuple[str, str]] = []
        for p in parts:
            pg = gate(p) or gate(strip_qualifiers(p))  # "neoadjuvant PD-1 antibody" is a class label too
            if pg:
                dropped.append((p, pg))
                continue
            k = _key_single(p)
            if len(k) < 3:
                dropped.append((p, "component_too_short"))
                continue
            keep.append((k, p))
        if not keep:
            return Keyed(
                raw=raw,
                cleaned=cleaned,
                key=None,
                gate_reason="all_components_gated",
                dropped_parts=dropped,
                route="combo",
            )
        keep.sort()
        keys = []
        surfaces = []
        for k, p in keep:
            if k not in keys:
                keys.append(k)
                surfaces.append(p)
        if len(keys) == 1:
            return Keyed(
                raw=raw,
                cleaned=cleaned,
                key=keys[0],
                dropped_parts=dropped,
                route="biologic" if is_biologic_shape(surfaces[0]) else "fixed_point",
            )
        return Keyed(
            raw=raw,
            cleaned=cleaned,
            key="+".join(keys),
            components=keys,
            component_surfaces=surfaces,
            dropped_parts=dropped,
            is_combo=True,
            route="combo",
        )

    if known_tokens and not is_biologic_shape(cleaned):
        toks = strip_dose_forms(strip_salt(strip_device(strip_qualifiers(cleaned)))).split()
        if len(toks) >= 2 and toks[0] not in _lex()["cations"]:
            tkeys = [_NONALNUM.sub("", t) for t in toks]
            form = _lex()["form_words"]
            if (
                all(t in known_tokens and t not in form for t in tkeys) and len(set(tkeys)) >= 2
            ):  # "abiraterone acetate" / "nicotine gum" are one drug, never a regimen
                first_surface = {}
                for t, k in zip(toks, tkeys, strict=True):
                    first_surface.setdefault(k, t)
                keys = sorted(first_surface)
                return Keyed(
                    raw=raw,
                    cleaned=cleaned,
                    key="+".join(keys),
                    components=keys,
                    component_surfaces=[first_surface[k] for k in keys],
                    is_combo=True,
                    route="combo_regimen",
                )
    key = _key_single(cleaned)
    if len(key) < 3:
        return Keyed(raw=raw, cleaned=cleaned, key=None, gate_reason="too_short_after_key", route="gated")
    post = strip_qualifiers(cleaned)
    g = gate(post) or ("qualifiers_only" if only_qualifiers(cleaned) else None)
    if g:  # the name was ONLY qualifiers, or qualifiers around a class word: "neoadjuvant chemotherapy"
        return Keyed(raw=raw, cleaned=cleaned, key=None, gate_reason=g, route="gated")
    return Keyed(
        raw=raw, cleaned=cleaned, key=key, route="biologic" if is_biologic_shape(cleaned) else "fixed_point"
    )


def dedup_key(raw: str) -> str | None:
    return route(raw).key
