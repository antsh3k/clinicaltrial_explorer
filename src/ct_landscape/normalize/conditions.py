"""Condition strings → folded keys, denoise reasons, and MeSH-heading → area rollup (spec §5.3, App. B.4–B.5).

Two condition surfaces share one `condition_key` column, labeled by `source`:
  - `mesh_leaf`  key = MeSH descriptor id from conditionBrowseModule.meshes[]
  - `listed`     key = order-preserving fold of the free-text conditions[] string
Ancestors are never a counting surface. A child→parent rewrite is never persisted.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass

from ct_landscape.normalize.lexicons import load

_STOPWORDS = {"the", "of", "and", "with", "due", "to", "a", "an", "in", "for", "or"}
_DASHES = "‐‑‒–—―-"
_LEADING_BULLETS = re.compile(r"^[\s•*·.\-–—>]+")
_TRAILING_PAREN = re.compile(r"\s*(\([^()]*\)|\[[^\[\]]*\])\s*$")
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")
_POSSESSIVE = re.compile(r"'s\b")

MESH_ID = re.compile(r"^[cd]\d{5,9}$", re.IGNORECASE)


def fold(raw: str) -> str:
    """ASCII-fold, lowercase, normalize apostrophes/dashes, strip bullets, drop possessive, iteratively peel
    trailing parentheticals, punctuation → space, collapse, drop stopwords — TOKEN ORDER PRESERVED."""
    s = unicodedata.normalize("NFKD", raw or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("’", "'").replace("‘", "'")
    for d in _DASHES:
        s = s.replace(d, " ")
    s = _LEADING_BULLETS.sub("", s)
    s = _POSSESSIVE.sub("", s)
    while True:
        m = _TRAILING_PAREN.search(s)
        if not m or m.start() == 0:
            break
        s = s[: m.start()]
    s = _PUNCT.sub(" ", s)
    toks = [t for t in _WS.split(s.strip()) if t and t not in _STOPWORDS]
    return " ".join(toks)


# ---------------------------------------------------------------- denoise (first match wins)

_KEEP = re.compile(
    r"disease|disorder|syndrome|itis\b|osis\b|opathy|emia\b|penia\b|oma\b|deficiency|failure|cancer|carcinoma|"
    r"tumou?r|leukemia|leukaemia|lymphoma|myeloma|infection|injury|pain|fibrosis|sclerosis|dystrophy|atrophy|"
    r"malignan|sarcoma|glioma|melanoma|neoplasm|metasta|stenosis|thrombo|ischemi|infarct|stroke|hypertension|"
    r"diabetes|obesity|asthma|copd|arthritis|psoriasis|dermatitis|eczema|lupus|crohn|colitis|hepatitis|cirrhosis|"
    r"epilepsy|seizure|migraine|depress|schizophren|anxiety|autism|dementia|alzheimer|parkinson|insomnia|"
    r"apnea|apnoea|anemia|anaemia|sepsis|pneumonia|covid|hiv|tuberculosis|malaria|fracture|burn|wound|ulcer|"
    r"insufficiency|dysfunction|impairment|abnormal|defect|lesion|cyst|polyp|hernia|aneurysm|embol|edema|oedema"
)
_REASONS: list[tuple[str, re.Pattern, bool]] = [  # (reason, regex on folded string, guarded by KEEP?)
    ("mesh_id_artifact", re.compile(r"^[cd]\d{5,9}$"), False),
    (
        "healthy_volunteers",
        re.compile(
            r"\bhealthy\b( (volunteers?|subjects?|participants?|adults?|people|individuals?|controls?|persons?|men|women|children|donors?))?$|^healthy$"
        ),
        False,
    ),
    (
        "behavior_qol_only",
        re.compile(
            r"quality life|adherence|satisfaction|knowledge|attitude|behavio(u)?r|lifestyle|wellbeing|well being|education|awareness|compliance|self efficacy|perception"
        ),
        True,
    ),
    (
        "lab_biomarker_only",
        re.compile(
            r"^[a-z0-9 ]*\b(positive|negative|mutation|amplification|overexpression|expression|wild ?type|status|levels?)$"
        ),
        True,
    ),
    (
        "device_procedure_only",
        re.compile(
            r"catheter|implant|prosthes|surgical technique|anesthesia|anaesthesia|imaging|ultrasound|endoscop|intubation|ventilation|dialysis access|vaccination|immunization|screening|monitoring"
        ),
        True,
    ),
    ("too_short", re.compile(r"^.{0,2}$"), False),
]


def denoise_reason(folded: str) -> str | None:
    """Return the first-matching drop reason, or None to keep. The middle reasons are gated by the disease-noun
    KEEP regex so an ambiguous string that also names a disease survives."""
    for reason, rx, guarded in _REASONS:
        if rx.search(folded):
            if guarded and _KEEP.search(folded):
                continue
            return reason
    return None


# ---------------------------------------------------------------- area rollup


@dataclass(frozen=True)
class AreaRule:
    heading: str
    area: str
    priority: int


@functools.cache
def area_rules() -> tuple[dict[str, AreaRule], set[str], str]:
    lx = load("mesh_areas")
    rules = {row["heading"]: AreaRule(row["heading"], row["area"], i) for i, row in enumerate(lx["areas"])}
    return rules, set(lx.get("excluded_headings", [])), lx["unclassified_area"]


def areas_for(ancestor_terms: list[str]) -> list[tuple[str, bool]]:
    """Map a condition's ancestor headings → [(area, is_primary)]. First-present-wins priority; all present
    areas kept (polyhierarchy); excluded headings ignored; nothing present → [] (caller assigns Unclassified)."""
    rules, excluded, _ = area_rules()
    hits = sorted(
        {
            rules[t].area: rules[t].priority for t in ancestor_terms if t in rules and t not in excluded
        }.items(),
        key=lambda kv: kv[1],
    )
    seen: list[tuple[str, bool]] = []
    for i, (area, _) in enumerate(hits):
        seen.append((area, i == 0))
    return seen


def unclassified_area() -> str:
    return area_rules()[2]
