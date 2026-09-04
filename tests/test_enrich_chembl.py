"""ChEMBL exact-fold join on a hand-built payload over the mini build (offline)."""

import json
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from ct_landscape.db import apply_views, create_enrich_schema
from ct_landscape.enrich.chembl import join, ship
from ct_landscape.enrich.load import load_shipped_enrichment
from ct_landscape.enrich.models import AssetEnrichment
from ct_landscape.ingest import ingest
from ct_landscape.normalize.build import normalize

MINI = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "mini.zip"

PAYLOAD = {
    "mechanisms": [
        {
            "mec_id": 1,
            "molecule_chembl_id": "CHEMBL3137343",
            "parent_molecule_chembl_id": "CHEMBL3137343",
            "target_chembl_id": "CHEMBL3307223",
            "mechanism_of_action": "Programmed cell death protein 1 inhibitor",
            "action_type": "INHIBITOR",
            "max_phase": 4,
        },
        {
            "mec_id": 2,
            "molecule_chembl_id": "CHEMBL1201585",
            "parent_molecule_chembl_id": "CHEMBL1201585",
            "target_chembl_id": "CHEMBL3307223",
            "mechanism_of_action": "Programmed cell death protein 1 inhibitor",
            "action_type": "INHIBITOR",
            "max_phase": 4,
        },
        {
            "mec_id": 3,
            "molecule_chembl_id": "CHEMBL9",
            "parent_molecule_chembl_id": "CHEMBL9",
            "target_chembl_id": "CHEMBL999",
            "mechanism_of_action": "Mystery target inhibitor",
            "action_type": "INHIBITOR",
        },
    ],
    "molecules": {
        "CHEMBL3137343": {
            "pref_name": "PEMBROLIZUMAB",
            "molecule_type": "Antibody",
            "max_phase": 4,
            "synonyms": [["Keytruda", "TRADE_NAME"], ["MK-3475", "RESEARCH_CODE"], ["Pembrolizumab", "INN"]],
        },
        "CHEMBL1201585": {
            "pref_name": "NIVOLUMAB",
            "molecule_type": "Antibody",
            "max_phase": 4,
            "synonyms": [["Opdivo", "TRADE_NAME"], ["Pembrolizumab", "OTHER"]],
        },  # a bogus shared synonym → ambiguity
        "CHEMBL9": {
            "pref_name": "ZZZ-NOT-IN-CORPUS",
            "molecule_type": "Small molecule",
            "max_phase": 1,
            "synonyms": [],
        },
    },
    "targets": {
        "CHEMBL3307223": {
            "pref_name": "Programmed cell death protein 1",
            "target_type": "SINGLE PROTEIN",
            "organism": "Homo sapiens",
            "gene_symbols": ["PDCD1"],
            "gene_symbols_other": ["PD1"],
        },
        "CHEMBL999": {
            "pref_name": "Mystery",
            "target_type": "SINGLE PROTEIN",
            "organism": "Homo sapiens",
            "gene_symbols": ["MYST1"],
            "gene_symbols_other": [],
        },
    },
}


@pytest.fixture(scope="module")
def con():
    con = duckdb.connect(":memory:")
    sink = open("/dev/null", "w")
    ingest(MINI, con, workers=1, log=sink)
    normalize(con, log=sink, workers=1)
    create_enrich_schema(con, drop=True)
    return con


def test_exact_fold_join_with_ambiguity_veto(con, tmp_path):
    payload = json.loads(json.dumps(PAYLOAD))
    payload["mechanisms"][1]["target_chembl_id"] = "CHEMBL999"  # make the two molecules' mechanisms DIFFER
    census = join(con, payload, log=open("/dev/null", "w"))
    # pembrolizumab's alias set matches CHEMBL3137343 via pref_name/INN/MK-3475 AND CHEMBL1201585 via the bogus
    # synonym; their mechanisms differ → real ambiguity → vetoed, logged, not applied
    assert census["n_ambiguous_asset_to_many_molecules"] == 1
    assert con.execute("SELECT count(*) FROM chembl_moa WHERE asset_id='pembrolizumab'").fetchone()[0] == 0
    assert any(
        "pembrolizumab" in s for s in census["skipped_examples"]
    )  # nivolumab's own clean match may still land
    # same aliases, but identical mechanism signatures (both PD-1 inhibitors) → labeled, counted as same-mechanism
    census = join(con, PAYLOAD, log=open("/dev/null", "w"))
    assert census["n_asset_to_many_molecules_same_mechanism"] == 1
    assert con.execute("SELECT count(*) FROM chembl_moa WHERE asset_id='pembrolizumab'").fetchone()[0] >= 1
    # targets vocabulary is seeded regardless: ChEMBL symbols + curated aliases
    syms = {r[0] for r in con.execute("SELECT symbol FROM targets").fetchall()}
    assert {"PDCD1", "MYST1", "CD274", "ERBB2"} <= syms
    assert con.execute("SELECT symbol FROM target_aliases WHERE alias_key='pdl1'").fetchone()[0] == "CD274"
    assert con.execute("SELECT symbol FROM target_aliases WHERE alias_key='pd1'").fetchone()[0] == "PDCD1"


def test_clean_join_produces_edges_and_ships(con, tmp_path):
    payload = json.loads(json.dumps(PAYLOAD))
    payload["molecules"]["CHEMBL1201585"]["synonyms"] = [["Opdivo", "TRADE_NAME"]]  # remove the bogus synonym
    census = join(con, payload, log=open("/dev/null", "w"))
    assert census["n_matched"] >= 1
    rows = con.execute(
        "SELECT asset_id, mechanism_of_action, action_type, target_symbols, moa_key FROM chembl_moa WHERE asset_id='pembrolizumab'"
    ).fetchall()
    assert rows and rows[0][2] == "INHIBITOR" and rows[0][3] == ["PDCD1"]
    assert "pdcd1" in rows[0][4].split("|")
    assert con.execute("SELECT match_via FROM asset_chembl WHERE asset_id='pembrolizumab'").fetchone()[0] in (
        "pref_name",
        "synonym",
    )
    # ship → reload from the artifact into a fresh enrichment schema → same edges; v_moa carries chembl provenance
    out = tmp_path / "chembl_moa.jsonl"
    edges = con.execute(
        "SELECT asset_id, mechanism_of_action, action_type, target_symbols, chembl_target_ids, edge_key FROM chembl_moa"
    ).fetchall()
    ship(
        [
            {
                "asset_id": a,
                "chembl_id": "CHEMBL3137343",
                "chembl_pref_name": "PEMBROLIZUMAB",
                "matched_alias": "Pembrolizumab",
                "match_via": "pref_name",
                "mechanism_of_action": m,
                "action_type": act,
                "target_symbols": ts,
                "chembl_target_ids": ti,
                "edge_key": ek,
            }
            for a, m, act, ts, ti, ek in edges
        ],
        out,
    )
    create_enrich_schema(con, drop=True)
    census2 = load_shipped_enrichment(con, tmp_path, log=open("/dev/null", "w"))
    assert census2["chembl"]["n_loaded_edges"] == len(edges)
    counts = apply_views(con, fail_on_empty=False)
    assert counts["v_moa"] >= 1
    prov = con.execute("SELECT provenance FROM v_moa_best WHERE asset_id='pembrolizumab'").fetchone()[0]
    assert prov == "chembl"


def test_enrichment_model_self_consistency():
    ok = AssetEnrichment(
        asset_id="x",
        known_entity="yes",
        basis="well_known_drug",
        targets=["PD-1"],
        action="inhibitor",
        moa_class="PD-1 inhibitor",
        confidence="high",
    )
    assert ok.self_consistent
    assert not AssetEnrichment(
        asset_id="x", known_entity="no", basis="insufficient", abstain=True, targets=["PD-1"]
    ).self_consistent
    assert not AssetEnrichment(asset_id="x", known_entity="yes", basis="insufficient").self_consistent
    assert not AssetEnrichment(
        asset_id="x", known_entity="yes", basis="name_stem_inference", confidence="high"
    ).self_consistent
    assert not AssetEnrichment(asset_id="x", known_entity="no", basis="trial_context").self_consistent
    with pytest.raises(ValidationError):
        AssetEnrichment(asset_id="x", known_entity="yes", basis="well_known_drug", bogus=1)  # extra="forbid"
