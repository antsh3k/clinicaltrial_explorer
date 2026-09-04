"""End-to-end ingest of the shipped mini.zip into an in-memory DuckDB (offline)."""

import json
from pathlib import Path

import duckdb
import pytest

from ct_landscape.db import read_meta
from ct_landscape.ingest import ingest

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "fixtures"
MINI = FIXTURES / "mini.zip"
MANIFEST = FIXTURES / "mini.manifest.json"


@pytest.fixture(scope="module")
def con():
    con = duckdb.connect(":memory:")
    ingest(MINI, con, workers=1, log=open("/dev/null", "w"))
    return con


def test_exact_member_count_reconciles(con):
    manifest = json.loads(MANIFEST.read_text())
    n = con.execute("SELECT count(*) FROM studies").fetchone()[0]
    assert n == manifest["n_members"]
    meta = read_meta(con)
    assert meta["ingest_census"]["n_read"] == n == meta["ingest_census"]["n_loaded"]
    assert meta["ingest_failures"] == []
    assert con.execute("SELECT count(*) FROM ingest_failures").fetchone()[0] == 0


def test_snapshot_date_comes_from_data(con):
    meta = read_meta(con)
    mx = con.execute("SELECT max(last_update_date_parsed) FROM studies").fetchone()[0]
    assert meta["snapshot_date"] == str(mx)


def test_anchor_study_fields(con):
    row = con.execute(
        "SELECT phase_norm, overall_status, study_type, org_class FROM studies WHERE nct_id='NCT02142738'"
    ).fetchone()
    assert row == ("PHASE3", "COMPLETED", "INTERVENTIONAL", "INDUSTRY")
    other = con.execute(
        "SELECT list(other_name_raw ORDER BY other_name_raw) FROM intervention_other_names "
        "WHERE nct_id='NCT02142738' AND intervention_no=0"
    ).fetchone()[0]
    assert "MK-3475" in other
    assert (
        con.execute(
            "SELECT count(*) FROM mesh_terms WHERE nct_id='NCT02142738' AND module='condition' AND kind='mesh' AND mesh_id='D002289'"
        ).fetchone()[0]
        == 1
    )


def test_messiness_cases_present(con):
    """The fixture must carry every §2.5 case the normalize tests will rely on."""
    q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    assert q("SELECT count(*) FROM arms WHERE type='PLACEBO_COMPARATOR'") > 0
    assert q("SELECT count(*) FROM arms WHERE type='OTHER'") > 0
    assert q("SELECT count(*) FROM interventions WHERE type='COMBINATION_PRODUCT'") > 0
    assert q(r"SELECT count(*) FROM interventions WHERE regexp_matches(name_raw, '.+\s\+\s.+')") > 0
    assert q("SELECT count(*) FROM studies WHERE study_type='OBSERVATIONAL'") > 0
    assert q("SELECT count(*) FROM studies WHERE study_type='EXPANDED_ACCESS'") > 0
    assert q("SELECT count(*) FROM studies WHERE date_precision='month'") > 0
    assert q("SELECT count(*) FROM studies WHERE overall_status='UNKNOWN'") > 0
    assert q("SELECT count(*) FROM studies WHERE nct_id NOT IN (SELECT nct_id FROM arms)") > 0
    assert (
        q(
            "SELECT count(*) FROM study_conditions WHERE regexp_matches(lower(name_raw), '^(juvenile|pediatric|paediatric|childhood) ')"
        )
        > 0
    )
    assert q("SELECT count(*) FROM sponsors WHERE role='collaborator'") > 0


def test_every_arm_link_resolves(con):
    dangling = con.execute(
        """SELECT count(*) FROM arm_interventions ai
           LEFT JOIN arms a USING (nct_id, arm_no) LEFT JOIN interventions i USING (nct_id, intervention_no)
           WHERE a.nct_id IS NULL OR i.nct_id IS NULL"""
    ).fetchone()[0]
    assert dangling == 0
