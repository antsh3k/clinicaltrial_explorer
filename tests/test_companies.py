import pytest

from ct_landscape.normalize.companies import canonical_display, company_key, declared_parent, pop_suffixes


@pytest.mark.parametrize(
    "a, b",
    [
        ("Pfizer", "Pfizer Inc."),
        ("Pfizer Inc.", "Pfizer, Inc"),
        ("Novartis Pharmaceuticals", "Novartis"),
        ("Novartis Pharmaceuticals Corporation", "Novartis AG"),
        ("Astellas Pharma Inc", "Astellas Pharma Global Development, Inc."),
        ("Genentech, Inc.", "Hoffmann-La Roche"),  # curated group
        ("Merck Sharp & Dohme LLC", "MSD"),
        ("Merck Sharp & Dohme LLC", "Merck & Co., Inc. (Rahway, New Jersey USA)"),
        ("Janssen Research & Development, LLC", "Johnson & Johnson"),
        ("Bristol-Myers Squibb", "Celgene"),  # dated acquisition
        ("GlaxoSmithKline", "GSK"),
        ("Sanofi", "Genzyme, a Sanofi Company"),  # declared parent
        ("Pfizer", "Hospira, now a wholly owned subsidiary of Pfizer"),
        ("Pfizer", "Wyeth is now a wholly owned subsidiary of Pfizer"),
        ("Pfizer", "Seagen, a wholly owned subsidiary of Pfizer"),
        (
            "Merck Sharp & Dohme LLC",
            "Cubist Pharmaceuticals LLC, a subsidiary of Merck & Co., Inc. (Rahway, New Jersey USA)",
        ),
        ("GSK", "Stiefel, a GSK Company"),
        ("Allergan", "Naurex, Inc, an affiliate of Allergan plc"),
        ("Eli Lilly and Company", "Lilly"),
        ("Takeda", "Baxalta now part of Shire"),
    ],
)
def test_same_company(a, b):
    assert company_key(a) == company_key(b), (company_key(a), company_key(b))


@pytest.mark.parametrize(
    "a, b",
    [
        ("Merck Sharp & Dohme LLC", "Merck KGaA, Darmstadt, Germany"),  # the two Mercks stay distinct
        ("Novartis", "Sandoz"),  # spun out
        ("Novartis", "Alcon"),
        ("Novartis AG", "Novartis AG; University of Glasgow"),  # no substring equality
        ("Pfizer", "Pfizer Foundation"),  # 'foundation' is not a popped token → distinct
        ("Abbott", "AbbVie"),
        ("Cancer Research UK", "Cancer Research Institute"),
    ],
)
def test_distinct_companies(a, b):
    assert company_key(a) != company_key(b)


def test_declared_parent_extraction():
    assert declared_parent("Genzyme, a Sanofi Company") == "Sanofi"
    assert declared_parent("Kite, A Gilead Company") == "Gilead"
    assert (
        declared_parent("Immune Design, a subsidiary of Merck & Co., Inc. (Rahway, New Jersey USA)")
        == "Merck & Co., Inc."
    )
    # comma-less declared parent (live-eval G03: this string survived as its own company id)
    assert declared_parent("Celgene a wholly owned subsidiary of BMS") == "BMS"
    assert company_key("Celgene a wholly owned subsidiary of BMS") == company_key("Bristol-Myers Squibb")
    assert company_key("Juno Therapeutics, a Subsidiary of Celgene") == company_key("Bristol-Myers Squibb")
    assert company_key("Wyeth is now a wholly owned subsidiary of Pfizer") == "pfizer"
    # chained declarations resolve to the ultimate parent (37 full-corpus trials keyed on the middle link before)
    assert (
        declared_parent(
            "ArQule, Inc., a subsidiary of Merck Sharp & Dohme LLC, a subsidiary of Merck & Co., Inc. (Rahway, NJ USA)"
        )
        == "Merck & Co., Inc."
    )
    # the article is required, so an academic "Division of X" is never read as a parent declaration
    assert declared_parent("Cardiology Division of Harvard University") is None
    assert declared_parent("Division of Cardiology, Boston Medical Center") is None
    assert declared_parent("Pfizer") is None
    assert (
        declared_parent("Mochida Pharmaceutical Company, Ltd.") is None
    )  # 'Company' as a legal form, not a parent


def test_suffix_pop_never_empties_the_name():
    assert pop_suffixes("pharma") == "pharma"
    assert pop_suffixes("acme pharmaceuticals inc") == "acme"
    assert pop_suffixes("acme life sciences ltd") == "acme"


def test_canonical_display_for_curated_groups():
    assert (
        canonical_display(company_key("Janssen Research & Development, LLC"), "x")
        == "Johnson & Johnson (Janssen)"
    )
    assert canonical_display(company_key("Acme Pharma Inc."), "Acme Pharma Inc.") == "Acme Pharma Inc."


def test_generic_words_are_not_popped_into_a_stub():
    assert (
        company_key("Cancer Research UK") == "cancer research"
    )  # geographic words pop, generic words do not
    assert company_key("Fred Hutchinson Cancer Research Center") == "fred hutchinson cancer research center"
    assert company_key("Janssen Research & Development, LLC") == company_key(
        "Johnson & Johnson"
    )  # curated group still wins
