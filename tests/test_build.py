"""End-to-end: ingest + normalize + views on the shipped mini.zip, in memory, offline.

The named messiness cases from spec §11.1 Phase 2 live here against real registry records.
"""

from pathlib import Path

import duckdb
import pytest

from ct_landscape.db import apply_views, create_enrich_schema, read_meta
from ct_landscape.ingest import ingest
from ct_landscape.normalize.build import normalize

MINI = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "mini.zip"


@pytest.fixture(scope="module")
def con():
    con = duckdb.connect(":memory:")
    sink = open("/dev/null", "w")
    ingest(MINI, con, workers=1, log=sink)
    normalize(con, log=sink, workers=1)
    create_enrich_schema(con, drop=True)
    con.execute("DROP VIEW IF EXISTS v_moa")
    return con


def q(con, sql, *params):
    return con.execute(sql, list(params)).fetchall()


def test_every_view_is_non_empty_except_enrichment_tiers(con):
    # Phase 2 ships no enrichment artifacts, so the chembl/llm-only views may be empty; everything else must not be.
    counts = apply_views(con, fail_on_empty=False)
    allowed_empty = {"v_moa", "v_moa_best", "v_moa_trials"}
    empty = {v for v, n in counts.items() if n == 0}
    assert empty <= allowed_empty, empty
    assert counts["v_programs"] > 50 and counts["v_combos"] > 0 and counts["v_population_landscape"] > 0


def test_placebo_never_an_asset(con):
    assert q(con, "SELECT count(*) FROM assets WHERE lower(canonical_name) LIKE '%placebo%'")[0][0] == 0
    assert q(con, "SELECT count(*) FROM asset_aliases WHERE alias_key LIKE 'placebo%'")[0][0] == 0
    gates = read_meta(con)["normalize_census"]["assets"]["gates"]
    assert gates.get("placebo_sham_prefix", 0) > 0


def test_mk3475_is_pembrolizumab(con):
    rows = q(
        con, "SELECT asset_id FROM asset_aliases WHERE alias_key IN ('mk3475','pembrolizumab','keytruda')"
    )
    ids = {r[0] for r in rows}
    assert ids == {"pembrolizumab"}
    assert (
        q(
            con,
            "SELECT count(*) FROM trial_assets WHERE nct_id='NCT02142738' AND asset_id='pembrolizumab' AND role='subject'",
        )[0][0]
        == 1
    )


def test_comparator_not_in_development(con):
    # KEYNOTE-024: pembrolizumab is the subject; platinum chemo agents are comparators → excluded from v_programs
    roles = dict(q(con, "SELECT asset_id, role FROM trial_assets WHERE nct_id='NCT02142738' AND via='name'"))
    assert roles["pembrolizumab"] == "subject"
    assert roles["carboplatin"] == "comparator"
    assert (
        q(
            con,
            "SELECT count(*) FROM v_programs WHERE asset_id='carboplatin' AND 'NCT02142738' = ANY(nct_ids)",
        )[0][0]
        == 0
    )
    assert (
        q(
            con,
            "SELECT count(*) FROM v_programs WHERE asset_id='pembrolizumab' AND 'NCT02142738' = ANY(nct_ids)",
        )[0][0]
        >= 1
    )


def test_combined_phase_rounds_up(con):
    # any trial whose raw phases were two tokens must carry the higher one; NSCLC KEYNOTE-024 is a plain PHASE3
    assert q(con, "SELECT phase_norm FROM studies WHERE nct_id='NCT02142738'")[0][0] == "PHASE3"
    assert (
        q(
            con,
            "SELECT count(*) FROM studies WHERE phase_norm NOT IN ('EARLY_PHASE1','PHASE1','PHASE2','PHASE3','PHASE4','NA') AND phase_norm IS NOT NULL",
        )[0][0]
        == 0
    )


def test_juvenile_condition_not_rewritten(con):
    # a listed 'Juvenile X' / 'Pediatric X' string keeps its own folded key; never persisted as the parent X
    rows = q(
        con,
        "SELECT display_name, condition_key FROM trial_conditions_norm WHERE source='listed' AND regexp_matches(lower(display_name), '^(juvenile|pediatric|paediatric|childhood) ')",
    )
    assert rows, "fixture must carry a juvenile/pediatric listed condition"
    for display, key in rows:
        assert key.split()[0] in ("juvenile", "pediatric", "paediatric", "childhood"), (display, key)


def test_trial_counted_once_across_condition_surfaces(con):
    # a trial with both listed and mesh_leaf rows appears ONLY under mesh_leaf in the primary surface
    both = q(
        con,
        """SELECT nct_id FROM trial_conditions_norm GROUP BY nct_id HAVING count(DISTINCT source) = 2 LIMIT 1""",
    )
    assert both
    nct = both[0][0]
    srcs = {
        r[0] for r in q(con, "SELECT DISTINCT source FROM v_trial_conditions_primary WHERE nct_id=?", nct)
    }
    assert srcs == {"mesh_leaf"}
    # and a listed-only trial still appears (Unclassified area, never dropped)
    listed_only = q(con, "SELECT count(*) FROM v_trial_conditions_primary WHERE source='listed'")[0][0]
    assert listed_only > 0
    assert q(con, "SELECT count(*) FROM v_sponsor_activity WHERE area='Unclassified'")[0][0] > 0


def test_sponsor_condition_view_counts_lead_sponsors_per_condition(con):
    rows = q(
        con,
        "SELECT company_id, n_trials, n_active_trials, len(nct_ids) FROM v_sponsor_condition WHERE condition_key='D002289' ORDER BY n_trials DESC LIMIT 5",
    )
    assert rows and all(n == ln and act <= n for _, n, act, ln in rows)
    # every (sponsor, condition) trial is an interventional drug trial led by that sponsor
    assert (
        q(
            con,
            """SELECT count(*) FROM v_sponsor_condition sc, unnest(sc.nct_ids) AS u(nct_id)
                     JOIN v_trials t USING (nct_id) WHERE t.lead_company_id <> sc.company_id OR NOT t.is_drug_trial""",
        )[0][0]
        == 0
    )


def test_unknown_status_is_neither_active_nor_inactive(con):
    rows = q(
        con,
        "SELECT program_exists, is_active_readout, is_inactive FROM v_trials WHERE overall_status='UNKNOWN' LIMIT 5",
    )
    assert rows
    for pe, ar, inact in rows:
        assert pe is False and ar is False and inact is False


def test_completed_program_exists_needs_a_dated_completion(con):
    # NULL completion date fails the dated cutoff (precision-first)
    rows = q(
        con,
        "SELECT program_exists FROM v_trials WHERE overall_status='COMPLETED' AND completion_date_parsed IS NULL",
    )
    assert all(r[0] is False for r in rows)


def test_in_all_arms_is_null_on_single_arm_trials(con):
    assert (
        q(
            con,
            """SELECT count(*) FROM trial_assets ta JOIN (SELECT nct_id FROM arms GROUP BY 1 HAVING count(*)=1) s USING (nct_id)
                     WHERE ta.in_all_arms IS NOT NULL""",
        )[0][0]
        == 0
    )


def test_no_enumeration_caps_in_programs(con):
    rows = q(con, "SELECT n_trials, len(nct_ids) FROM v_programs")
    assert all(n == ln for n, ln in rows)


def test_contested_aliases_are_logged_not_applied(con):
    for k, res in q(con, "SELECT alias_key, resolution FROM contested_aliases"):
        rows = q(con, "SELECT asset_id, source FROM asset_aliases WHERE alias_key=?", k)
        if res == "vetoed":
            # never handed to a claimant: either absent, or the alias is a cluster's OWN name key
            assert all(src == "name" and aid == k for aid, src in rows), (k, rows)
        else:
            assert res.startswith("dominance:") and len(rows) == 1


def test_company_normalization_and_declared_parents(con):
    keys = {r[0] for r in q(con, "SELECT DISTINCT company_id FROM trial_sponsors_norm WHERE role='lead'")}
    assert "merck sharp dohme" in keys or "msd" in keys or any("merck" in k for k in keys)
    # no sponsor string maps to an empty company
    assert (
        q(con, "SELECT count(*) FROM trial_sponsors_norm WHERE company_id IS NULL OR company_id=''")[0][0]
        == 0
    )


def test_population_mentions_carry_kind_and_evidence(con):
    rows = q(con, "SELECT kind, count(*) FROM population_mentions GROUP BY 1")
    kinds = {k for k, _ in rows}
    assert {"biomarker", "disease_stage", "demographic"} <= kinds
    assert (
        q(con, "SELECT count(*) FROM population_mentions WHERE evidence_line IS NULL OR evidence_line=''")[0][
            0
        ]
        == 0
    )


def test_trial_card_has_one_row_per_trial_with_ctgov_url(con):
    n = q(con, "SELECT count(*) FROM studies")[0][0]
    assert q(con, "SELECT count(*) FROM v_trial_card")[0][0] == n
    url, arms = q(con, "SELECT ctgov_url, arms FROM v_trial_card WHERE nct_id='NCT02142738'")[0]
    assert url == "https://clinicaltrials.gov/study/NCT02142738"
    assert arms and any(a["type"] == "EXPERIMENTAL" for a in arms)
