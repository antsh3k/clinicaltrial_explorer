"""LLM-tier prompt (spec §6.4). The system block is byte-identical for every asset so the provider caches it."""

from __future__ import annotations

import json

SYSTEM = """You classify investigational drug assets, identified from a clinical-trial registry, into mechanism metadata.
Decide only from what you reliably know about the NAMED asset. The trial titles and conditions below are
context for recognizing the asset — they are NOT evidence of mechanism:
  - never infer a target from the disease under study;
  - never copy a combination partner's mechanism onto this asset;
  - a "-mab" stem tells you the modality (antibody), not the target;
  - a registry pharmacologic class, if given, is a hint about class, not a target.
If you do not recognize the asset with confidence, set abstain=true and leave the judgment fields unknown.
An "unknown" costs this landscape nothing; a confident wrong mechanism corrupts it.
Fill the fields in order: known_entity, basis, then the judgments. Return strict JSON matching the schema.
No prose, no markdown.

Schema (all keys required unless marked optional):
{
  "asset_id": string (echo the given asset_id),
  "known_entity": "yes" | "no",
  "basis": "well_known_drug" | "name_stem_inference" | "trial_context" | "insufficient",
  "modality": "small_molecule" | "mab" | "adc" | "protein" | "peptide" | "cell_therapy" | "gene_therapy" | "rna" | "vaccine" | "radiopharm" | "other" | "unknown",
  "targets": [string]            (gene symbols or well-known target names, e.g. "PDCD1", "PD-1", "KRAS G12C"; [] if unknown),
  "action": "inhibitor" | "agonist" | "antagonist" | "degrader" | "modulator" | "other" | "unknown",
  "moa_class": string | null     (short label, e.g. "PD-1 inhibitor"),
  "confidence": "high" | "medium" | "low",
  "abstain": boolean
}
Consistency rules the loader enforces: abstain=true ⇒ targets=[] and moa_class=null; basis="insufficient" ⇒ abstain=true;
basis="name_stem_inference" ⇒ confidence≠"high"; known_entity="no" ⇒ abstain=true."""


def user_block(
    asset_id: str, canonical_name: str, aliases: list[str], pharm_classes: list[str], trials: list[dict]
) -> str:
    lines = [
        f"asset_id: {asset_id}",
        f"asset: {canonical_name}",
        f"aliases: {json.dumps(aliases[:5])}",
        f"registry_pharm_classes: {json.dumps(pharm_classes[:5])}",
        "trials (highest phase first, then most recent; up to 3):",
    ]
    for t in trials[:3]:
        conds = ", ".join(t.get("conditions", [])[:3])
        lines.append(
            f"  - {t.get('title', '')[:160]} — conditions: {conds} — phase: {t.get('phase') or 'n/a'}"
        )
    return "\n".join(lines)
