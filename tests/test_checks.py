from ct_landscape.evals.checks import CheckResult, Pooled, Role, roll_up, set_prf


def test_set_prf_pinned_edge_cases():
    assert set_prf(frozenset(), frozenset()) == (1.0, 1.0, 1.0)
    p, r, _ = set_prf(frozenset({"a"}), frozenset())
    assert (p, r) == (0.0, 1.0)
    p, r, _ = set_prf(frozenset(), frozenset({"a"}))
    assert (p, r) == (1.0, 0.0)
    p, r, f = set_prf(frozenset({"a", "b", "c"}), frozenset({"b", "c", "d"}))
    assert (round(p, 3), round(r, 3), round(f, 3)) == (0.667, 0.667, 0.667)


def test_pooled_is_not_a_macro_mean():
    pool = Pooled()
    pool.add("one_item", frozenset({"x"}), frozenset({"x"}))  # perfect on a 1-item gold
    pool.add(
        "forty", frozenset(), frozenset({f"g{i}" for i in range(40)})
    )  # nothing returned on a 40-item gold
    assert pool.recall() == 1 / 41  # a macro mean would report 0.5
    assert pool.precision() == 1.0
    res = pool.results("nct_set", "agent")
    assert {r.metric: r.role for r in res} == {
        "nct_set_precision": Role.OBJ,
        "nct_set_recall": Role.OBJ,
        "nct_set_f1": Role.DIAG,
    }
    assert next(r for r in res if r.metric == "nct_set_recall").denominator == 41


def test_roll_up_floors_cannot_be_traded_for_obj():
    results = [
        CheckResult(
            metric="ungrounded_citation_count",
            value=1,
            role=Role.FLOOR,
            section="agent",
            detail=[{"case": "G07"}],
        ),
        CheckResult(metric="nct_set_precision", value=0.99, role=Role.OBJ, section="agent"),
        CheckResult(metric="tokens", value=1e6, role=Role.DIAG, section="agent"),
    ]
    out = roll_up(results)
    assert not out.passed and out.floor_breaches == ["ungrounded_citation_count"] and out.obj_score == 0.99
    assert roll_up([r for r in results if r.role is not Role.FLOOR]).passed
    assert roll_up(results, {"ungrounded_citation_count": 1}).passed  # an explicit threshold pin
