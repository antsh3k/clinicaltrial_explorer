"""Lexicon loader: every controlled list lives as YAML under `lexicons/` so changes are reviewable data."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

LEXICON_DIR = Path(__file__).resolve().parents[3] / "lexicons"


@functools.cache
def load(name: str) -> dict[str, Any]:
    path = LEXICON_DIR / f"{name}.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data
