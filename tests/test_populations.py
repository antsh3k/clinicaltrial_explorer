from ct_landscape.normalize.populations import KINDS, entries, find_mentions


def test_lexicon_loads_with_valid_kinds():
    es = entries()
    assert len(es) > 150
    assert {e.kind for e in es} == set(KINDS)
    assert len({e.term_id for e in es}) == len(es)  # term ids unique


def test_biomarkers_and_subgroups_are_typed_with_evidence():
    title = "Sotorasib in First-Line Treatment of Metastatic KRAS G12C-Mutated NSCLC"
    elig = (
        "Inclusion Criteria:\n"
        "- Age ≥ 18 years\n"
        "- Documented KRAS G12C mutation\n"
        "- PD-L1 TPS >= 50%\n"
        "- Platinum-pretreated patients are eligible\n"
        "Exclusion Criteria:\n"
        "- Prior EGFR TKI therapy\n"
    )
    ms = find_mentions(title, ["Non-Small Cell Lung Cancer", "Carcinoma"], elig)
    by = {(m.term_id, m.surface): m for m in ms}
    assert ("KRAS_G12C", "title") in by and ("KRAS_G12C", "eligibility") in by
    assert by[("KRAS_G12C", "eligibility")].evidence_line.startswith("- Documented KRAS G12C")
    assert by[("KRAS_G12C", "title")].kind == "biomarker"
    assert ("PDL1", "eligibility") in by
    assert ("FIRST_LINE", "title") in by and by[("FIRST_LINE", "title")].kind == "line_of_therapy"
    assert ("METASTATIC", "title") in by and by[("METASTATIC", "title")].kind == "disease_stage"
    assert ("PLATINUM_PRETREATED", "eligibility") in by and by[
        ("PLATINUM_PRETREATED", "eligibility")
    ].kind == "prior_therapy"
    assert ("ADULT", "eligibility") not in by  # not a lexicon term; structured stdAges covers it


def test_relapsed_refractory_is_one_term_not_three():
    ms = find_mentions("Relapsed/Refractory Multiple Myeloma", [], None)
    ids = {m.term_id for m in ms}
    assert "RELAPSED_REFRACTORY" in ids
    assert "RELAPSED" not in ids and "REFRACTORY" not in ids


def test_moderate_to_severe_is_not_also_moderate_and_severe():
    ms = find_mentions("Moderate-to-Severe Plaque Psoriasis", [], None)
    ids = {m.term_id for m in ms}
    assert "MODERATE_TO_SEVERE" in ids and "MODERATE" not in ids and "SEVERE" not in ids


def test_no_text_no_mentions():
    assert find_mentions(None, [], None) == []
