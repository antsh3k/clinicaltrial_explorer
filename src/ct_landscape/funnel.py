"""Completeness funnel (spec §8.5) — printed by `ctl build`, pasted into the README as real numbers.

Every number here is a claim the eval can audit; the API's /api/meta restates the load-bearing ones.
"""

from __future__ import annotations

import sys
from typing import Any

import duckdb

from ct_landscape.db import read_meta, write_meta


def compute_funnel(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    meta = read_meta(con)
    ing = meta.get("ingest_census", {})
    nz = meta.get("normalize_census", {})
    f: dict[str, Any] = {"snapshot_date": meta.get("snapshot_date")}
    f["studies_ingested"] = q("SELECT count(*) FROM studies")
    f["zip_members"] = ing.get("n_members")
    f["parse_failures"] = ing.get("n_parse_failures", 0)
    f["interventional"] = q("SELECT count(*) FROM studies WHERE study_type='INTERVENTIONAL'")
    f["drug_bio_trials"] = q("SELECT count(*) FROM v_trials WHERE is_drug_trial")
    f["interventional_drug_bio"] = q(
        "SELECT count(*) FROM v_trials WHERE is_drug_trial AND study_type='INTERVENTIONAL'"
    )
    f["industry_lead"] = q("SELECT count(*) FROM v_trials WHERE is_industry")
    f["in_scope_industry_interventional_drug"] = q(
        "SELECT count(*) FROM v_trials WHERE is_industry AND is_drug_trial AND study_type='INTERVENTIONAL'"
    )
    a = nz.get("assets", {})
    f["interventions_drug_bio"] = a.get("n_interventions_in")
    f["interventions_gated"] = a.get("n_interventions_gated")
    f["interventions_keyed"] = a.get("n_interventions_keyed")
    f["gates"] = a.get("gates", {})
    f["assets"] = q("SELECT count(*) FROM assets WHERE NOT is_combo")
    f["combo_assets"] = q("SELECT count(*) FROM assets WHERE is_combo")
    f["merged_via_other_names"] = a.get("n_merged_via_other_names")
    f["contested_aliases"] = q("SELECT count(*) FROM contested_aliases WHERE resolution = 'vetoed'")
    f["alias_dominance_resolutions"] = q(
        "SELECT count(*) FROM contested_aliases WHERE resolution LIKE 'dominance:%'"
    )
    f["in_scope_trial_asset_rows"] = q(
        """SELECT count(*) FROM trial_assets ta JOIN v_trials t USING (nct_id)
           WHERE t.is_industry AND t.is_drug_trial AND t.study_type='INTERVENTIONAL' AND ta.role IN ('subject','unknown') AND ta.via='name'"""
    )
    f["in_scope_trial_asset_rows_with_moa"] = q(
        """SELECT count(*) FROM trial_assets ta JOIN v_trials t USING (nct_id)
           WHERE t.is_industry AND t.is_drug_trial AND t.study_type='INTERVENTIONAL' AND ta.role IN ('subject','unknown') AND ta.via='name'
             AND ta.asset_id IN (SELECT asset_id FROM v_moa)"""
    )
    f["pct_in_scope_trial_asset_rows_moa_labeled"] = round(
        100 * f["in_scope_trial_asset_rows_with_moa"] / max(f["in_scope_trial_asset_rows"], 1), 1
    )
    f["in_scope_assets"] = q(
        """SELECT count(DISTINCT ta.asset_id) FROM trial_assets ta JOIN v_trials t USING (nct_id)
           WHERE t.is_industry AND t.is_drug_trial AND t.study_type='INTERVENTIONAL' AND ta.role IN ('subject','unknown')"""
    )
    f["pct_drug_interventions_to_assets"] = round(
        100 * (a.get("n_interventions_keyed") or 0) / max(a.get("n_interventions_in") or 1, 1), 1
    )
    c = nz.get("conditions", {})
    f["pct_trials_with_mesh_leaf"] = c.get("pct_trials_with_mesh_leaf")
    f["trials_listed_only"] = c.get("n_trials_listed_only")
    f["condition_denoise_drops"] = c.get("denoise_reasons", {})
    f["pct_drug_trials_with_arms"] = round(
        100
        * q(
            "SELECT count(*) FROM v_trials t WHERE t.is_drug_trial AND t.study_type='INTERVENTIONAL' AND EXISTS (SELECT 1 FROM arms a WHERE a.nct_id=t.nct_id)"
        )
        / max(f["interventional_drug_bio"], 1),
        1,
    )
    roles = a.get("roles", {})
    tot = sum(roles.values()) or 1
    f["pct_trial_asset_role_decidable"] = round(
        100 * (roles.get("subject", 0) + roles.get("comparator", 0)) / tot, 1
    )
    f["roles"] = roles
    p = nz.get("populations", {})
    f["pct_trials_with_population_mention_by_kind"] = p.get("pct_trials_with_mention_by_kind", {})
    f["moa_assets_chembl"] = q("SELECT count(DISTINCT asset_id) FROM chembl_moa")
    f["moa_assets_nlm_class"] = q("SELECT count(DISTINCT asset_id) FROM asset_nlm_classes")
    f["moa_assets_llm"] = q("SELECT count(*) FROM asset_enrichment WHERE NOT abstained")
    f["moa_assets_llm_abstained"] = q("SELECT count(*) FROM asset_enrichment WHERE abstained")
    f["in_scope_assets_with_any_moa"] = q(
        """SELECT count(DISTINCT ta.asset_id) FROM trial_assets ta JOIN v_trials t USING (nct_id)
           WHERE t.is_industry AND t.is_drug_trial AND t.study_type='INTERVENTIONAL' AND ta.role IN ('subject','unknown')
             AND ta.asset_id IN (SELECT asset_id FROM v_moa)"""
    )
    f["pct_in_scope_assets_moa_labeled"] = round(
        100 * f["in_scope_assets_with_any_moa"] / max(f["in_scope_assets"], 1), 1
    )
    write_meta(con, {"funnel": f})
    return f


def print_funnel(f: dict[str, Any], log=sys.stderr) -> None:
    n = lambda k: f"{f.get(k) or 0:,}"  # noqa: E731
    lines = [
        "completeness funnel:",
        f"  {n('studies_ingested')} studies ingested (= {n('zip_members')} zip members; {n('parse_failures')} parse failures) · snapshot {f.get('snapshot_date')}",
        f"    → {n('interventional')} interventional → {n('interventional_drug_bio')} drug/bio interventional → {n('industry_lead')} industry-lead → {n('in_scope_industry_interventional_drug')} in scope (industry ∩ interventional ∩ drug/bio)",
        f"  interventions: {n('interventions_drug_bio')} drug/bio names → −{n('interventions_gated')} gated {f.get('gates')}",
        f"    → {n('interventions_keyed')} keyed ({f.get('pct_drug_interventions_to_assets')}%) → {n('assets')} assets + {n('combo_assets')} combos; {n('merged_via_other_names')} merged via otherNames; {n('alias_dominance_resolutions')} aliases assigned by dominance; {n('contested_aliases')} contested (vetoed)",
        f"    → {n('in_scope_assets')} in-scope assets → MoA labeled: chembl {n('moa_assets_chembl')} · nlm_class {n('moa_assets_nlm_class')} · llm {n('moa_assets_llm')} (abstained {n('moa_assets_llm_abstained')}) → {f.get('pct_in_scope_assets_moa_labeled')}% of in-scope assets carry ≥1 mechanism label ({f.get('pct_in_scope_trial_asset_rows_moa_labeled')}% of in-scope trial×asset rows)",
        f"  conditions: {f.get('pct_trials_with_mesh_leaf')}% of trials carry ≥1 MeSH leaf; {n('trials_listed_only')} listed-only; denoise drops {f.get('condition_denoise_drops')}",
        f"  arms: {f.get('pct_drug_trials_with_arms')}% of drug trials have arms; {f.get('pct_trial_asset_role_decidable')}% of (trial, asset) roles decidable {f.get('roles')}",
        f"  populations: % trials with ≥1 typed mention by kind: {f.get('pct_trials_with_population_mention_by_kind')}",
    ]
    print("\n".join(lines), file=log, flush=True)
