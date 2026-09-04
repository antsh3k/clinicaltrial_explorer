"""LLM-tier response model (spec §6.4): classification, not document extraction. Chain-of-thought as OUTPUT
FIELDS — recognition (known_entity, basis) before judgment — plus pure self-consistency post-gates."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class AssetEnrichment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    known_entity: Literal["yes", "no"]
    basis: Literal["well_known_drug", "name_stem_inference", "trial_context", "insufficient"]
    modality: Literal[
        "small_molecule",
        "mab",
        "adc",
        "protein",
        "peptide",
        "cell_therapy",
        "gene_therapy",
        "rna",
        "vaccine",
        "radiopharm",
        "other",
        "unknown",
    ] = "unknown"
    targets: list[str] = []
    action: Literal["inhibitor", "agonist", "antagonist", "degrader", "modulator", "other", "unknown"] = (
        "unknown"
    )
    moa_class: str | None = None
    confidence: Literal["high", "medium", "low"] = "low"
    abstain: bool = False

    @property
    def self_consistent(self) -> bool:
        """Reject verdicts that contradict their own fields."""
        if self.abstain and (self.targets or self.moa_class):
            return False
        if self.basis == "insufficient" and not self.abstain:
            return False
        if self.basis == "name_stem_inference" and self.confidence == "high":
            return False
        if self.known_entity == "no" and not self.abstain:
            return False
        return True
