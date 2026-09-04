import copy

import pytest

from ct_landscape.agent.gate import Answer, Citation, EntityRef, Table, gate, nct_refs_from_text

GOOD = Answer(
    answer_md="Pembrolizumab is studied with axitinib [NCT02853331] and lenvatinib [NCT02811861].",
    citations=[
        Citation(nct_id="NCT02853331", why="axitinib combo"),
        Citation(nct_id="NCT02811861", why="lenvatinib combo"),
    ],
    entities=[EntityRef(kind="drug", id="pembrolizumab"), EntityRef(kind="condition", id="D002292")],
    table=Table(
        columns=["partner", "nct"], rows=[["axitinib", "NCT02853331"], ["lenvatinib", "NCT02811861"]]
    ),
    caveats=["arm-level combos only"],
)
RETRIEVED = {"NCT02853331", "NCT02811861", "NCT02142738"}
SEEN = {"pembrolizumab", "D002292", "axitinib"}


def test_nct_scanner_tolerates_separators_but_not_digit_counts():
    refs = nct_refs_from_text("see NCT02853331, NCT-02811861, nct 02142738 and NCT1234567 and NCT123456789")
    assert refs[0] == ("NCT02853331", "NCT02853331", True)
    assert refs[1] == ("NCT02811861", "NCT-02811861", True)
    assert refs[2] == ("NCT02142738", "nct 02142738", True)
    assert refs[3][2] is False and refs[4][2] is False


def test_clean_answer_passes():
    assert gate(GOOD, RETRIEVED, SEEN) == []


# ---- §8.4 mutation mini-suite: one planted defect per case, selected by precondition, never by index


def _seed() -> Answer:
    return copy.deepcopy(GOOD)


def mutate_fabricated_nct(a: Answer) -> Answer:
    assert "NCT09999999" not in a.answer_md
    a.answer_md += " Also see NCT09999999."
    return a


def mutate_seven_digit_nct(a: Answer) -> Answer:
    a.answer_md += " And NCT1234567."
    return a


def mutate_citation_outside_retrieved(a: Answer) -> Answer:
    assert a.citations, "seed must carry a citation to mutate"
    a.citations[0] = Citation(nct_id="NCT00000001", why="planted")
    return a


def mutate_entity_never_returned(a: Answer) -> Answer:
    assert a.entities, "seed must carry an entity to mutate"
    a.entities.append(EntityRef(kind="drug", id="nivolumab"))
    return a


def mutate_table_cell_nct(a: Answer) -> Answer:
    assert a.table is not None
    a.table.rows.append(["planted", "NCT08888888"])
    return a


@pytest.mark.parametrize(
    "mutate, expect",
    [
        (mutate_fabricated_nct, "NCT never retrieved: NCT09999999"),
        (mutate_seven_digit_nct, "malformed NCT: NCT1234567"),
        (mutate_citation_outside_retrieved, "fabricated citation: NCT00000001"),
        (mutate_entity_never_returned, "entity never in a tool result: drug:nivolumab"),
        (mutate_table_cell_nct, "NCT never retrieved: NCT08888888"),
    ],
)
def test_each_planted_defect_breaks_exactly_its_floor(mutate, expect):
    a = mutate(_seed())
    errs = gate(a, RETRIEVED, SEEN)
    assert errs == [expect], errs


def test_no_mutation_control():
    assert gate(_seed(), RETRIEVED, SEEN) == []


def test_malformed_seed_fails_loud():
    empty = Answer(answer_md="nothing", citations=[], entities=[])
    with pytest.raises(AssertionError):
        mutate_citation_outside_retrieved(empty)


def test_existing_in_index_is_not_enough():
    # 'axitinib' is a real asset but was NOT in any tool result of this conversation → violation
    a = _seed()
    a.entities.append(EntityRef(kind="drug", id="axitinib"))
    assert gate(a, RETRIEVED, SEEN - {"axitinib"}) == ["entity never in a tool result: drug:axitinib"]


def test_answer_forbids_extra_fields():
    with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
        Answer(answer_md="x", bogus=1)
