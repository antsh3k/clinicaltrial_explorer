"""Asset assembly + arm-role tests on hand-built rows (spec §5.1–5.2 messiness cases)."""

from ct_landscape.normalize.arms import assign_roles, role_for
from ct_landscape.normalize.assets import InterventionRow, build_assets


def _iv(nct, no, name, typ="DRUG"):
    return InterventionRow(nct, no, typ, name)


def test_mk3475_is_pembrolizumab():
    ivs = [
        _iv("NCT1", 0, "Pembrolizumab"),
        _iv("NCT1b", 0, "Pembrolizumab"),  # a cluster-to-cluster merge needs ≥2 asserting trials
        _iv("NCT2", 0, "MK-3475"),
        _iv("NCT3", 0, "KEYTRUDA"),
        _iv("NCT4", 0, "Placebo"),
    ]
    other = {
        ("NCT1", 0): ["MK-3475", "SCH 900475", "KEYTRUDA®"],
        ("NCT1b", 0): ["MK-3475", "KEYTRUDA®"],
        ("NCT3", 0): ["pembrolizumab"],
    }
    r = build_assets(ivs, other)
    ids = {
        r.intervention_assets[("NCT1", 0)][0][0],
        r.intervention_assets[("NCT2", 0)][0][0],
        r.intervention_assets[("NCT3", 0)][0][0],
    }
    assert len(ids) == 1
    aid = ids.pop()
    assert aid == "pembrolizumab"
    assert r.assets[aid]["canonical_name"] == "Pembrolizumab (KEYTRUDA)"
    assert r.assets[aid]["n_trials"] == 4
    assert r.aliases["mk3475"] == (aid, "MK-3475", "other_name") or r.aliases["mk3475"][0] == aid
    assert r.aliases["sch900475"][0] == aid
    assert ("NCT4", 0) not in r.intervention_assets  # placebo never an asset
    assert r.gate_census["placebo_sham_prefix"] == 1
    assert not r.contested


def test_contested_alias_is_vetoed_not_merged():
    ivs = [_iv("NCT1", 0, "Alphaxin"), _iv("NCT2", 0, "Betaxin"), _iv("NCT3", 0, "Gammaxin")]
    # both A and B claim the junk alias "xyzzy"; C is unrelated
    other = {("NCT1", 0): ["xyzzy"], ("NCT2", 0): ["xyzzy"]}
    r = build_assets(ivs, other)
    a = r.intervention_assets[("NCT1", 0)][0][0]
    b = r.intervention_assets[("NCT2", 0)][0][0]
    assert a != b
    assert [c[0] for c in r.contested] == ["xyzzy"] and r.contested[0][3] == "vetoed"
    assert set(r.contested[0][1]) == {"alphaxin", "betaxin"}
    assert "xyzzy" not in r.aliases  # global uniqueness: a contested alias belongs to nobody


def test_dominance_rule_assigns_brand_to_the_real_asset_and_logs_it():
    # 12 trials name pembrolizumab and list Keytruda; one trial names a typo (also listing Keytruda); one names KEYTRUDA
    ivs = [_iv(f"NCT{i}", 0, "Pembrolizumab") for i in range(12)] + [
        _iv("NCTX", 0, "Pembroluzimab"),
        _iv("NCTY", 0, "KEYTRUDA"),
    ]
    other = {(f"NCT{i}", 0): ["Keytruda"] for i in range(12)}
    other[("NCTX", 0)] = ["Keytruda"]
    r = build_assets(ivs, other)
    assert r.aliases["keytruda"][0] == "pembrolizumab"  # brand assigned to the dominant claimant
    assert r.intervention_assets[("NCTY", 0)][0][0] == "pembrolizumab"  # the KEYTRUDA-named cluster merged in
    assert r.intervention_assets[("NCTX", 0)][0][0] == "pembroluzimab"  # the typo cluster stays separate
    assert [(c[0], c[3]) for c in r.contested] == [("keytruda", "dominance:pembrolizumab")]
    assert r.census["n_alias_dominance_resolutions"] == 1 and r.census.get("n_contested_aliases", 0) == 0


def test_dominance_rule_needs_five_trials_and_ten_x():
    ivs = [_iv(f"NCT{i}", 0, "Alphaxin") for i in range(4)] + [_iv("NCTX", 0, "Betaxin")]
    other = {(f"NCT{i}", 0): ["Zbrand"] for i in range(4)}
    other[("NCTX", 0)] = ["Zbrand"]
    r = build_assets(ivs, other)
    assert "zbrand" not in r.aliases and r.contested[0][3] == "vetoed"  # 4 < 5 trials → still vetoed


def test_multi_claimant_alias_resolves_when_claimants_already_merged():
    # A and B are the same drug: A lists B as an otherName (uncontested merge); both list brand "Z"
    ivs = [_iv("NCT1", 0, "Alphadrug"), _iv("NCT1b", 0, "Alphadrug"), _iv("NCT2", 0, "AD-123")]
    other = {("NCT1", 0): ["AD-123", "Zbrand"], ("NCT1b", 0): ["AD-123"], ("NCT2", 0): ["Zbrand"]}
    r = build_assets(ivs, other)
    assert r.intervention_assets[("NCT1", 0)][0][0] == r.intervention_assets[("NCT2", 0)][0][0]
    assert r.aliases["zbrand"][0] == "alphadrug"
    assert not r.contested


def test_combo_keeps_component_edges_and_links_components():
    ivs = [
        _iv("NCT1", 0, "Carbidopa/Levodopa"),
        _iv("NCT2", 0, "Levodopa"),
        _iv("NCT3", 0, "Pembrolizumab + MK-1234"),
    ]
    r = build_assets(ivs, {})
    links = r.intervention_assets[("NCT1", 0)]
    assert links[0] == ("carbidopa+levodopa", "name")
    assert ("levodopa", "combo_component") in links and ("carbidopa", "combo_component") in links
    assert r.assets["carbidopa+levodopa"]["is_combo"] is True
    assert ("carbidopa+levodopa", "levodopa") in r.components
    assert r.assets["levodopa"]["n_trials"] == 2  # standalone + as component
    assert r.assets["mk1234"]["canonical_name"] == "MK-1234"  # code-name surface kept when nothing better


def test_canonical_name_prefers_non_code_surface():
    ivs = [
        _iv("NCT1", 0, "MK-3475"),
        _iv("NCT2", 0, "MK-3475"),
        _iv("NCT3", 0, "pembrolizumab"),
        _iv("NCT4", 0, "pembrolizumab"),
    ]
    other = {("NCT3", 0): ["MK-3475"], ("NCT4", 0): ["MK-3475"]}
    r = build_assets(ivs, other)
    aid = r.intervention_assets[("NCT1", 0)][0][0]
    assert r.assets[aid]["canonical_name"] == "pembrolizumab"
    assert (
        aid == r.assets[aid]["dedup_key"] == "pembrolizumab"
    )  # the id follows the canonical surface, never a code


def test_salt_forms_collapse_but_biosimilars_do_not():
    ivs = [
        _iv("NCT1", 0, "Doxorubicin"),
        _iv("NCT2", 0, "Doxorubicin hydrochloride"),
        _iv("NCT3", 0, "Trastuzumab"),
        _iv("NCT4", 0, "Trastuzumab-dkst", "BIOLOGICAL"),
    ]
    r = build_assets(ivs, {})
    assert r.intervention_assets[("NCT1", 0)][0][0] == r.intervention_assets[("NCT2", 0)][0][0]
    assert r.intervention_assets[("NCT3", 0)][0][0] != r.intervention_assets[("NCT4", 0)][0][0]


# ---------------------------------------------------------------- roles


def test_role_is_subject_first_and_other_is_neither():
    assert role_for(["EXPERIMENTAL"]) == "subject"
    assert role_for(["EXPERIMENTAL", "ACTIVE_COMPARATOR"]) == "subject"
    assert role_for(["ACTIVE_COMPARATOR"]) == "comparator"
    assert role_for(["PLACEBO_COMPARATOR", "NO_INTERVENTION"]) == "comparator"
    assert role_for(["OTHER"]) == "unknown"
    assert role_for(["OTHER", "ACTIVE_COMPARATOR"]) == "unknown"
    assert role_for([]) == "unknown"
    assert role_for([None]) == "unknown"


def test_comparator_not_in_development_and_in_all_arms_semantics():
    ia = {
        ("NCT1", 0): [("pembrolizumab", "name")],
        ("NCT1", 1): [("docetaxel", "name")],
        ("NCT1", 2): [("carboplatin", "name")],
    }
    arm_links = {("NCT1", 0): [0], ("NCT1", 1): [1], ("NCT1", 2): [0, 1]}
    arm_types = {("NCT1", 0): "EXPERIMENTAL", ("NCT1", 1): "ACTIVE_COMPARATOR"}
    rows = {(r[2]): (r[4], r[5]) for r in assign_roles(ia, arm_links, arm_types, {"NCT1": 2})}
    assert rows["pembrolizumab"] == ("subject", False)
    assert rows["docetaxel"] == ("comparator", False)
    assert rows["carboplatin"] == ("subject", True)  # background therapy in every arm


def test_single_arm_trial_has_null_in_all_arms():
    ia = {("NCT1", 0): [("x", "name")]}
    rows = assign_roles(ia, {("NCT1", 0): [0]}, {("NCT1", 0): "EXPERIMENTAL"}, {"NCT1": 1})
    assert rows[0][4] == "subject" and rows[0][5] is None


def test_armless_record_is_unknown():
    ia = {("NCT1", 0): [("x", "name")]}
    rows = assign_roles(ia, {}, {}, {})
    assert rows[0][4] == "unknown" and rows[0][5] is None


def test_single_trial_enumeration_does_not_merge_two_real_assets():
    # NCT01103440-style: a junk-ish intervention lists two different antiplatelets as "other names"
    ivs = [_iv(f"NCT{i}", 0, "Tirofiban") for i in range(3)] + [
        _iv(f"NCTC{i}", 0, "Cangrelor") for i in range(3)
    ]
    ivs.append(_iv("NCTX", 0, "Glycoprotein inhibitor plus ASA"))
    other = {("NCTX", 0): ["Tirofiban", "Cangrelor"]}
    r = build_assets(ivs, other)
    assert r.intervention_assets[("NCT0", 0)][0][0] != r.intervention_assets[("NCTC0", 0)][0][0]
    assert r.census["n_merges_blocked_single_trial"] >= 1


def test_two_trials_asserting_a_synonym_still_merge():
    ivs = [_iv("NCT1", 0, "Pembrolizumab"), _iv("NCT2", 0, "Pembrolizumab"), _iv("NCT3", 0, "MK-3475")]
    other = {("NCT1", 0): ["MK-3475"], ("NCT2", 0): ["MK-3475"]}
    r = build_assets(ivs, other)
    assert r.intervention_assets[("NCT3", 0)][0][0] == "pembrolizumab"
