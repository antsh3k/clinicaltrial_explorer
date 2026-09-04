import pytest

from ct_landscape.normalize.mechanism_key import mechanism_key, mechanism_tokens


@pytest.mark.parametrize(
    "a, b",
    [
        ("anti-CD20 antibody", "CD20 inhibitor"),
        ("PD-1 inhibitor", "Anti PD-1 monoclonal antibody"),
        ("KRAS G12C inhibitors", "KRAS G12C inhibitor"),
        ("JAK1/2 inhibitor", "JAK1 and JAK2 inhibitor"),
        ("Programmed cell death 1 receptor antagonist", "programmed cell death 1"),
    ],
)
def test_fold_meets(a, b):
    assert mechanism_key(a) == mechanism_key(b) != ""


def test_antithrombin_survives_the_anti_strip():
    assert "antithrombin" in mechanism_tokens("antithrombin III")
    assert mechanism_tokens("anti-thrombin") == ["thrombin"]


def test_numeric_suffix_expansion():
    assert mechanism_tokens("JAK1/2") == ["jak1", "jak2"]
    assert mechanism_tokens("FGFR1/2/3 inhibitor") == ["fgfr1", "fgfr2", "fgfr3"]


def test_key_is_sorted_scalar():
    assert mechanism_key("VEGF and PDGF") == "pdgf|vegf" == mechanism_key("PDGF, VEGF")
    assert mechanism_key("inhibitor") == ""
