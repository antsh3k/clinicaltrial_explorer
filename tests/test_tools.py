"""resolve_entity ladder, SQL sandbox, get_trial — on the mini build, through a sandboxed file connection."""

from pathlib import Path

import duckdb
import pytest

from ct_landscape.agent import tools
from ct_landscape.db import apply_views, create_enrich_schema
from ct_landscape.enrich.load import load_shipped_enrichment
from ct_landscape.ingest import ingest
from ct_landscape.normalize.build import normalize

MINI = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "mini.zip"


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "mini.duckdb"
    con = duckdb.connect(str(path))
    sink = open("/dev/null", "w")
    ingest(MINI, con, workers=1, log=sink)
    normalize(con, log=sink, workers=1)
    create_enrich_schema(con, drop=True)
    load_shipped_enrichment(con, Path("/nonexistent"), log=sink)
    apply_views(con, fail_on_empty=False)
    con.close()
    return str(path)


@pytest.fixture
def con(db_path):
    c = tools.open_sandboxed(db_path)
    yield c
    c.close()


# ---------------------------------------------------------------- resolve_entity


def test_resolve_drug_by_alias_code(con):
    r = tools.resolve(con, "MK-3475", "drug")
    assert r.candidates and r.candidates[0].id == "pembrolizumab" and r.candidates[0].match == "alias"


def test_resolve_drug_exact_and_auto(con):
    assert tools.resolve(con, "pembrolizumab", "drug").candidates[0].match == "exact"
    auto = tools.resolve(con, "Pembrolizumab", "auto")
    assert auto.candidates[0].kind == "drug" and auto.candidates[0].id == "pembrolizumab"


def test_resolve_condition_prefers_mesh_key(con):
    r = tools.resolve(con, "Carcinoma, Non-Small-Cell Lung", "condition")
    assert r.candidates[0].id == "D002289"
    assert tools.resolve(con, "D002289", "condition").candidates[0].id == "D002289"
    tok = tools.resolve(
        con, "non-small-cell lung carcinoma", "condition"
    )  # word order differs from the MeSH term
    assert tok.candidates[0].id == "D002289" and tok.candidates[0].match == "tokens"
    r2 = tools.resolve(con, "non-small", "condition")
    assert any(c.id == "D002289" for c in r2.candidates) and r2.candidates[0].match in ("prefix", "contains")


def test_resolve_condition_abbreviation_and_lay_phrasing_reach_mesh(con):
    """Regression for live-eval G05/G06/G12: 'NSCLC' resolved to the listed-only key `nsclc` and
    'non-small cell lung cancer' returned ten listed strings and never D002289 (wrong keyspace)."""
    for q in ("NSCLC", "nsclc", "non-small cell lung cancer", "Non Small Cell Lung Cancer"):
        r = tools.resolve(con, q, "condition")
        assert r.candidates and r.candidates[0].id == "D002289", (q, r.candidates[:3])
        assert r.candidates[0].match == "curated"
    # the lay phrasing works through the generic fold too (cancer/carcinoma → neoplasm), not only the file
    r = tools.resolve(con, "renal cell cancer", "condition")
    assert r.candidates and r.candidates[0].id == "D002292"


def test_resolve_condition_strips_stage_qualifiers(con):
    for q in ("advanced non-small cell lung cancer", "metastatic NSCLC", "Stage IV NSCLC", "recurrent NSCLC"):
        r = tools.resolve(con, q, "condition")
        assert r.candidates and r.candidates[0].id == "D002289", (q, r.candidates[:3])
    assert tools.condition_tokens("advanced non-small cell lung cancer") == [
        "cell",
        "lung",
        "neoplasm",
        "non",
        "small",
    ]
    assert tools.condition_tokens("Carcinoma, Non-Small-Cell Lung") == [
        "cell",
        "lung",
        "neoplasm",
        "non",
        "small",
    ]
    assert (
        tools.condition_tokens("Lung Neoplasms")
        == tools.condition_tokens("lung cancer")
        == ["lung", "neoplasm"]
    )


def test_resolve_condition_reports_listed_only_sibling_never_sums(con):
    """When the MeSH key wins by synonym, a listed-only key for the same surface is a SECOND candidate with a note,
    so the two keyspaces stay visibly separate (§5.3)."""
    r = tools.resolve(con, "NSCLC", "condition")
    ids = [c.id for c in r.candidates]
    assert ids[0] == "D002289"
    if "nsclc" in ids:  # present whenever the fixture has a trial listing "NSCLC" without the MeSH leaf
        assert "never sum" in r.note and "nsclc" in r.note
        assert r.candidates[ids.index("nsclc")].match == "exact"
    else:
        assert r.note == ""


def test_condition_synonym_lexicon_is_consistent():
    syn = tools.condition_synonyms()
    assert syn["nsclc"][0] == "D002289" and syn["ipf"][0] == "D054990" and syn["rcc"][0] == "D002292"
    assert all(tools.MESH_ID.match(mesh_id) for mesh_id, _ in syn.values())
    # every surface folds to a non-empty key and nothing collides (the loader raises on a conflicting mapping)
    assert all(k for k in syn)


def test_resolve_company_via_alias_group(con):
    r = tools.resolve(con, "MSD", "company")
    assert r.candidates and "merck" in r.candidates[0].id


def test_resolve_population_and_moa(con):
    r = tools.resolve(con, "KRAS G12C", "population")
    assert r.candidates and r.candidates[0].id == "KRAS_G12C"
    m = tools.resolve(
        con, "checkpoint inhibitor", "moa"
    )  # nlm_class tier may or may not carry one in the mini build
    assert isinstance(m.candidates, list)


def test_resolve_not_found_is_loud_with_nearest(con):
    r = tools.resolve(con, "zzzzqqq-not-a-drug", "drug")
    assert r.candidates == [] and isinstance(r.nearest, list)


# ---------------------------------------------------------------- run_sql sandbox


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE studies",
        "SELECT 1; SELECT 2",
        "INSERT INTO build_meta VALUES ('x','y')",
        "COPY (SELECT 1) TO '/tmp/x.csv'",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SET memory_limit='1GB'",
        "PRAGMA database_list",
        "select * from read_text('/etc/hosts')",
        "",
        "-- comment only",
        "/* x */ DELETE FROM studies",
    ],
)
def test_sandbox_rejects_non_select(con, sql):
    with pytest.raises(tools.SqlRejected):
        tools.sandboxed_query(con, sql)


def test_sandbox_layer_two_blocks_filesystem_even_if_layer_four_slipped(con):
    # a read-only, external-access-off connection refuses file functions at the engine level too
    with pytest.raises((tools.SqlRejected, duckdb.Error)):
        con.execute("SELECT * FROM read_csv_auto('/etc/passwd')")


def test_run_sql_harvests_all_ids_before_truncation(con):
    res = tools.sandboxed_query(
        con, "SELECT nct_id, phase_norm FROM v_trials ORDER BY nct_id -- with a comment"
    )
    assert res.total_row_count > tools.ROW_CAP
    assert len(res.nct_ids) == res.total_row_count  # every id grounded, not just the first 200
    shaped = res.for_model()
    assert len(shaped["rows"]) == tools.ROW_CAP and shaped["truncated"] is True
    prog = tools.sandboxed_query(
        con, "SELECT asset_id, condition_key, nct_ids FROM v_programs WHERE asset_id='pembrolizumab'"
    )
    assert "pembrolizumab" in prog.entity_ids and any(k.startswith("D") for k in prog.entity_ids)
    assert prog.nct_ids  # ids inside list columns are harvested too


def test_run_sql_list_columns_are_truncated_for_the_model(con):
    res = tools.sandboxed_query(con, "SELECT list(nct_id) AS ids FROM v_trials")
    shaped = res.for_model()
    assert len(shaped["rows"][0][0]) == tools.LIST_HEAD + 1 and shaped["rows"][0][0][-1].startswith("… (+")
    assert len(res.nct_ids) > tools.LIST_HEAD


def test_run_sql_error_is_reported_as_rejection(con):
    with pytest.raises(tools.SqlRejected) as e:
        tools.sandboxed_query(con, "SELECT nope FROM v_trials")
    assert "Binder" in str(e.value) or "nope" in str(e.value)


# ---------------------------------------------------------------- get_trial


def test_get_trial_card_and_entity_ids(con):
    card = tools.get_trial(con, "NCT02142738")
    assert card and card["ctgov_url"].endswith("NCT02142738") and card["phase_norm"] == "PHASE3"
    ents = tools.trial_entity_ids(card)
    assert "pembrolizumab" in ents and "D002289" in ents
    assert tools.get_trial(con, "NCT00000000") is None
    with pytest.raises(tools.SqlRejected):
        tools.get_trial(con, "NCT123")
