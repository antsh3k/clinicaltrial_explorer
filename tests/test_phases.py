import pytest

from ct_landscape.normalize.phases import phase_norm, phase_rank


@pytest.mark.parametrize(
    "phases, expected",
    [
        (["PHASE2", "PHASE3"], "PHASE3"),
        (["PHASE1", "PHASE2"], "PHASE2"),
        (["PHASE3"], "PHASE3"),
        (["EARLY_PHASE1"], "EARLY_PHASE1"),
        (["NA"], "NA"),
        ([], None),
        (None, None),
        (["BOGUS"], None),
        (["phase1 "], "PHASE1"),
    ],
)
def test_combined_phase_rounds_up(phases, expected):
    assert phase_norm(phases) == expected


def test_na_beside_real_phase_does_not_win():
    assert phase_norm(["NA", "PHASE2"]) == "PHASE2"


def test_rank_ordinal_and_na_is_null():
    assert phase_rank("EARLY_PHASE1") == 0.5
    assert phase_rank("PHASE4") == 4.0
    assert phase_rank("NA") is None
    assert phase_rank(None) is None
