"""Gold loader, mutation operators, and the harness end-to-end on the mini build with scripted models (offline)."""

import json
from pathlib import Path

import duckdb
import pytest
import yaml
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart

from ct_landscape.agent.gate import Answer, Citation, EntityRef, Table, gate
from ct_landscape.db import apply_views, create_enrich_schema
from ct_landscape.enrich.load import load_shipped_enrichment
from ct_landscape.evals.gold import Gold, load_gold
from ct_landscape.evals.harness import answer_entity_surface, answer_nct_surface, run_eval
from ct_landscape.evals.mutate import OPERATORS
from ct_landscape.evals.replay import scripted_model
from ct_landscape.funnel import compute_funnel
from ct_landscape.ingest import ingest
from ct_landscape.normalize.build import normalize

MINI = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "mini.zip"


# ---------------------------------------------------------------- gold


def test_shipped_gold_loads_and_covers_every_archetype():
    g = load_gold()
    assert g.metadata.n_cases == len(g.cases) == 14
    assert {c.archetype for c in g.cases} >= {
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "Q5",
        "Q6",
        "Q7",
        "negative",
        "messiness",
    }
    assert sum(1 for c in g.cases if c.borderline) == 2
    assert all(c.expected.ncts == [] for c in g.cases if c.check == "nct_set" and not c.adjudicated)


def test_gold_typo_fails_at_the_boundary_naming_the_field(tmp_path):
    raw = yaml.safe_load(load_gold.__globals__["GOLD_PATH"].read_text())
    raw["cases"][0]["expcted"] = {"entities": []}
    p = tmp_path / "gold.yaml"
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValidationError) as e:
        load_gold(p)
    assert "expcted" in str(e.value)


def test_gold_case_count_mismatch_is_loud(tmp_path):
    raw = yaml.safe_load(load_gold.__globals__["GOLD_PATH"].read_text())
    raw["metadata"]["n_cases"] = 3
    p = tmp_path / "gold.yaml"
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):
        load_gold(p)


# ---------------------------------------------------------------- mutation operators (§8.4)

SEED = Answer(
    answer_md="Pembrolizumab with axitinib [NCT02853331].",
    citations=[Citation(nct_id="NCT02853331", why="combo")],
    entities=[EntityRef(kind="drug", id="pembrolizumab")],
    table=Table(columns=["partner", "nct"], rows=[["axitinib", "NCT02853331"]]),
)
RETRIEVED, SEEN = {"NCT02853331"}, {"pembrolizumab", "axitinib"}


@pytest.mark.parametrize("name", list(OPERATORS))
def test_each_operator_breaks_exactly_its_floor(name):
    op, expect = OPERATORS[name]
    assert gate(SEED, RETRIEVED, SEEN) == []  # control
    assert gate(op(SEED), RETRIEVED, SEEN) == [expect]
    assert gate(SEED, RETRIEVED, SEEN) == []  # operator is pure


def test_operator_refuses_a_seed_it_cannot_mutate():
    with pytest.raises(ValueError):
        OPERATORS["citation_outside_retrieved"][0](Answer(answer_md="x"))


# ---------------------------------------------------------------- harness on the mini build


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
    compute_funnel(con)
    con.close()
    return str(path)


MINI_GOLD = {
    "metadata": {"source": "synthetic", "as_of": "2026-09-04", "n_cases": 3},
    "cases": [
        {
            "id": "M01",
            "archetype": "Q7",
            "question": "partners of MK-3475?",
            "check": "contains_all",
            "expected": {"entities": ["carboplatin"], "asset_id": "pembrolizumab"},
            "adjudicated": True,
        },
        {
            "id": "M02",
            "archetype": "negative",
            "question": "pembrolizumab for Erdheim-Chester?",
            "check": "honest_empty",
            "expected": {"must_mention": ["no "]},
            "adjudicated": True,
        },
        {
            "id": "M03",
            "archetype": "Q1",
            "question": "an empty-set probe",
            "check": "entity_set",
            "expected": {"entities": ["pembrolizumab"]},
            "adjudicated": True,
        },
    ],
}


def _model():
    def fn(messages, info):
        returns = {}
        question = ""
        for m in messages:
            for p in getattr(m, "parts", []):
                if isinstance(p, ToolReturnPart):
                    returns[p.tool_name] = p.content
                if type(p).__name__ == "UserPromptPart":
                    question = str(p.content)
        if "Erdheim" in question:
            if "run_sql" not in returns:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "run_sql",
                            {
                                "sql": "SELECT nct_ids FROM v_programs WHERE asset_id='pembrolizumab' AND condition_key='D031249'"
                            },
                        )
                    ]
                )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "submit_answer",
                        {
                            "answer_md": "There are no trials of pembrolizumab in Erdheim-Chester disease in this index.",
                            "citations": [],
                            "entities": [],
                            "caveats": ["absence from the index is not evidence of absence"],
                        },
                    )
                ]
            )
        if "resolve_entity" not in returns:
            return ModelResponse(parts=[ToolCallPart("resolve_entity", {"query": "MK-3475", "kind": "drug"})])
        if "run_sql" not in returns:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_sql",
                        {
                            "sql": "SELECT partner_asset_id, count(DISTINCT nct_id) n, list(DISTINCT nct_id) ncts FROM v_combo_partners WHERE asset_id='pembrolizumab' GROUP BY 1 ORDER BY n DESC"
                        },
                    )
                ]
            )
        rows = returns["run_sql"]["result"]["rows"]
        table_rows = [[r[0], r[1], r[2][0]] for r in rows[:5]]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "submit_answer",
                    {
                        "answer_md": f"{len(rows)} partners.",
                        "citations": [{"nct_id": r[2], "why": "arm-level"} for r in table_rows],
                        "entities": [{"kind": "drug", "id": "pembrolizumab"}]
                        + [{"kind": "drug", "id": r[0]} for r in table_rows],
                        "table": {"columns": ["partner", "n_trials", "example"], "rows": table_rows},
                        "caveats": [],
                    },
                )
            ]
        )

    return scripted_model(fn)


def test_harness_scores_floors_obj_diag_and_writes_report(db_path, tmp_path):
    gold = Gold.model_validate(MINI_GOLD)
    out = tmp_path / "evalrun"
    report = run_eval(db_path, gold, model=_model(), mode="live", out_dir=out, log=open("/dev/null", "w"))
    assert report["passed"] is True and report["floor_breaches"] == []
    assert report["case_scores"]["M02"] == 1.0  # honest empty
    assert (
        report["case_scores"]["M01"] == 1.0
    )  # carboplatin is an arm-level partner of pembrolizumab in KEYNOTE-024
    assert (out / "M01.json").exists() and (out / "report.md").exists()
    rec = json.loads((out / "M01.json").read_text())
    assert rec["messages"] and rec["answer"]["table"]["rows"]
    metrics = {r["metric"]: r for r in report["results"]}
    assert metrics["entity_set_recall"]["role"] == "DIAG"  # pooled gold < 30 items → reported, not gated
    assert metrics["zero_result_path_count"]["value"] == 0
    # replay: the recorded transcripts drive the real agent again with no model → identical answers, no mismatch
    rep = run_eval(
        db_path, gold, mode="replay", out_dir=tmp_path / "replay", replay_dir=out, log=open("/dev/null", "w")
    )
    assert rep["passed"] and {r["metric"]: r["value"] for r in rep["results"]}["replay_mismatch_count"] == 0


def test_answer_surfaces_are_alias_tolerant():
    a = {
        "answer_md": "see NCT02142738 and NCT1234567",
        "citations": [{"nct_id": "NCT02853331", "why": ""}],
        "entities": [{"kind": "drug", "id": "pembrolizumab"}],
        "table": {"columns": ["asset", "n"], "rows": [["Lenvatinib (LENVIMA)", 3]]},
    }
    assert answer_nct_surface(a) == {"NCT02142738", "NCT02853331"}  # malformed id excluded
    assert {"pembrolizumab", "lenvatinib"} <= answer_entity_surface(a)
