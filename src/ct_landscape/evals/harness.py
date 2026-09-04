"""Eval harness (spec §8): drives the SAME answer_question() generator the API streams (no HTTP in the loop),
scores FLOOR / OBJ / DIAG checks, and writes a report with id lists — never just rates.

Modes:
  live    — the configured model (needs ANTHROPIC_API_KEY); answers are recorded to runs/evals/<stamp>/ and become
            replay fixtures
  replay  — recorded transcripts replayed through the real agent, tools and validator with a FunctionModel
            (replay_mismatch_count is what gates CI)
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import duckdb

from ct_landscape.agent import tools as T
from ct_landscape.agent.agent import Deps, answer_question
from ct_landscape.agent.gate import nct_refs_from_text
from ct_landscape.evals.checks import CheckResult, Pooled, Role, roll_up
from ct_landscape.evals.gold import Gold, GoldCase, load_gold
from ct_landscape.evals.replay import transcript_model

RUNS_EVALS = Path("runs/evals")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# ---------------------------------------------------------------- answer surfaces


def answer_entity_surface(answer: dict[str, Any]) -> set[str]:
    """Entity ids/names the answer asserts: table cells (first two columns) + entities[]; normalized."""
    out: set[str] = set()
    for e in answer.get("entities", []):
        out.add(_norm(e["id"]))
    tbl = answer.get("table")
    if tbl and tbl.get("rows"):
        for row in tbl["rows"]:
            for cell in row[:2]:
                if isinstance(cell, str) and not re.match(r"^NCT\d{8}$", cell):
                    out.add(_norm(cell))
                    # "Pembrolizumab (Keytruda)" → also the bare generic
                    out.add(_norm(cell.split("(")[0]))
    return {x for x in out if x}


def answer_nct_surface(answer: dict[str, Any]) -> set[str]:
    ncts = {c["nct_id"] for c in answer.get("citations", [])}
    text = (
        answer.get("answer_md", "")
        + "\n"
        + "\n".join(" ".join(str(c) for c in row) for row in (answer.get("table") or {}).get("rows", []))
    )
    ncts.update(canon for canon, _raw, ok in nct_refs_from_text(text) if ok)
    return ncts


def _mentions(answer: dict[str, Any], needle: str) -> bool:
    hay = (answer.get("answer_md", "") + " " + " ".join(answer.get("caveats", []))).lower()
    return needle.lower() in hay


def _entity_hit(expected: str, surface: set[str]) -> bool:
    e = _norm(expected)
    return any(e == s or (len(e) >= 4 and (e in s or s in e)) for s in surface)


# ---------------------------------------------------------------- per-case scoring


def score_case(case: GoldCase, outcome: dict[str, Any], con: duckdb.DuckDBPyConnection) -> list[CheckResult]:
    """outcome: {'answer': {...} | None, 'gate': {...}, 'trace': [...], 'error': str|None, 'usage': {...}}"""
    res: list[CheckResult] = []
    cid = case.id
    sec = f"case:{cid}"
    answer = outcome.get("answer")
    trace = outcome.get("trace", [])
    gate_v = (outcome.get("gate") or {}).get("violations", []) if answer else []

    # ---- FLOORs (computed from the FINAL answer; a rejected-then-corrected answer is clean by construction)
    res.append(
        CheckResult(
            metric="ungrounded_citation_count",
            role=Role.FLOOR,
            section=sec,
            value=sum(1 for v in gate_v if v.startswith(("NCT never retrieved", "fabricated citation"))),
            detail=[{"case": cid, "violation": v} for v in gate_v],
        )
    )
    res.append(
        CheckResult(
            metric="malformed_nct_count",
            role=Role.FLOOR,
            section=sec,
            value=sum(1 for v in gate_v if v.startswith("malformed NCT")),
            detail=[{"case": cid}],
        )
    )
    res.append(
        CheckResult(
            metric="ungrounded_entity_count",
            role=Role.FLOOR,
            section=sec,
            value=sum(1 for v in gate_v if v.startswith("entity never")),
            detail=[{"case": cid}],
        )
    )
    sql_steps = [t for t in trace if t.get("tool") == "run_sql" and "rows" in t]
    zero_path = int(
        bool(sql_steps) and all(t["rows"] == 0 for t in sql_steps) and case.check != "honest_empty"
    )
    res.append(
        CheckResult(
            metric="zero_result_path_count",
            role=Role.FLOOR,
            section=sec,
            value=zero_path,
            detail=[{"case": cid, "n_sql": len(sql_steps)}],
        )
    )
    if answer is None:
        res.append(
            CheckResult(
                metric="hard_failure_count",
                role=Role.FLOOR,
                section=sec,
                value=1,
                detail=[{"case": cid, "error": outcome.get("error")}],
            )
        )
        res.append(
            CheckResult(
                metric="case_score", role=Role.DIAG if case.borderline else Role.OBJ, section=sec, value=0.0
            )
        )
        return res
    res.append(CheckResult(metric="hard_failure_count", role=Role.FLOOR, section=sec, value=0))

    ents = answer_entity_surface(answer)
    ncts = answer_nct_surface(answer)
    exp = case.expected
    obj_role = Role.DIAG if case.borderline else Role.OBJ
    score = 1.0
    detail: list[dict] = []

    if case.check == "honest_empty":
        dishonest = int(
            bool(answer.get("citations"))
            or bool(
                answer.get("entities") and answer.get("table", {}) and (answer.get("table") or {}).get("rows")
            )
            or not any(_mentions(answer, m) for m in (exp.must_mention or ["no ", "not "]))
        )
        res.append(
            CheckResult(
                metric="dishonest_empty_count",
                role=Role.FLOOR,
                section=sec,
                value=dishonest,
                detail=[{"case": cid}],
            )
        )
        score = 1.0 - dishonest
    else:
        res.append(CheckResult(metric="dishonest_empty_count", role=Role.FLOOR, section=sec, value=0))

    if case.check in ("contains_all", "top_k_contains"):
        surface = ents
        if case.check == "top_k_contains" and answer.get("table") and exp.k:
            surface = set()
            for row in answer["table"]["rows"][: exp.k]:
                for cell in row[:2]:
                    if isinstance(cell, str):
                        surface.add(_norm(cell))
                        surface.add(_norm(cell.split("(")[0]))
            surface |= (
                {_norm(e["id"]) for e in answer.get("entities", [])} if not answer["table"]["rows"] else set()
            )
        hits = [e for e in exp.entities if _entity_hit(e, surface)]
        prose_hits: list[str] = []
        if (
            case.check == "contains_all"
        ):  # targets/mechanisms may legitimately live in prose; recorded, not hidden
            prose = _norm(answer.get("answer_md", ""))
            prose_hits = [
                e for e in exp.entities if e not in hits and len(_norm(e)) >= 4 and _norm(e) in prose
            ]
        misses = [e for e in exp.entities if e not in hits and e not in prose_hits]
        score = (len(hits) + len(prose_hits)) / len(exp.entities) if exp.entities else 1.0
        detail.append({"case": cid, "hits": hits, "prose_hits": prose_hits, "misses": misses})
    if case.check == "refuse_approval":
        bad = [m for m in exp.must_not_mention if _mentions(answer, m)]
        score = 0.0 if bad else 1.0
        detail.append({"case": cid, "forbidden_phrases_found": bad})
    if case.check in ("role_split", "states_rollup"):
        missing = [m for m in exp.must_mention if not _mentions(answer, m)]
        score = 1.0 - len(missing) / max(len(exp.must_mention), 1)
        detail.append({"case": cid, "missing_mentions": missing})
    if case.check == "reconcile":
        combo_n, prog_n = _reconcile_counts(con, exp.asset_id or "", exp.condition_key or "")
        nums = {int(x.replace(",", "")) for x in re.findall(r"\b\d[\d,]*\b", answer.get("answer_md", ""))}
        ok = combo_n in nums and prog_n in nums and combo_n <= prog_n
        score = 1.0 if ok else 0.0
        detail.append(
            {
                "case": cid,
                "sql_combo_trials": combo_n,
                "sql_program_trials": prog_n,
                "numbers_in_answer": sorted(nums)[:20],
            }
        )
    # must_mention applies to every check kind that declares it (caveat probes)
    if exp.must_mention and case.check not in ("role_split", "states_rollup", "honest_empty"):
        missing = [m for m in exp.must_mention if not _mentions(answer, m)]
        if missing:
            score *= 0.5
            detail.append({"case": cid, "missing_mentions": missing})
    res.append(
        CheckResult(metric="case_score", role=obj_role, section=sec, value=round(score, 4), detail=detail)
    )
    res.append(CheckResult(metric="answer_ncts", role=Role.DIAG, section=sec, value=len(ncts)))
    res.append(CheckResult(metric="answer_entities", role=Role.DIAG, section=sec, value=len(ents)))
    return res


def _reconcile_counts(con, asset_id: str, condition_key: str) -> tuple[int, int]:
    combo = con.execute(
        "SELECT count(DISTINCT nct_id) FROM v_combo_partners WHERE asset_id = ? AND condition_key = ?",
        [asset_id, condition_key],
    ).fetchone()[0]
    prog = con.execute(
        "SELECT coalesce(max(n_trials), 0) FROM v_programs WHERE asset_id = ? AND condition_key = ?",
        [asset_id, condition_key],
    ).fetchone()[0]
    return int(combo), int(prog)


# ---------------------------------------------------------------- running


async def run_case(db_path: str, case: GoldCase, model: Any | None) -> dict[str, Any]:
    con = T.open_sandboxed(db_path)
    deps = Deps(db=con)
    out: dict[str, Any] = {
        "case_id": case.id,
        "question": case.question,
        "answer": None,
        "gate": None,
        "trace": [],
        "error": None,
        "usage": None,
        "events": [],
        "messages": None,
        "elapsed_ms": None,
    }
    t0 = time.monotonic()
    try:
        async for ev in answer_question(deps, case.question, model=model):
            if ev["event"] == "answer":
                out.update(
                    answer=ev["answer"],
                    gate=ev["gate"],
                    trace=ev["trace"],
                    usage=ev["usage"],
                    messages=ev["new_messages"],
                )
            elif ev["event"] == "error":
                out.update(error=ev["error"], gate=ev.get("gate"), trace=ev.get("trace", []))
            elif ev["event"] in ("tool_call", "tool_result"):
                out["events"].append(
                    {k: v for k, v in ev.items() if k != "input"} | {"input": ev.get("input")}
                )
    finally:
        out["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        con.close()
    return out


def run_eval(
    db_path: str,
    gold: Gold | None = None,
    *,
    model: Any | None = None,
    mode: str = "live",
    case_ids: list[str] | None = None,
    out_dir: Path | None = None,
    replay_dir: Path | None = None,
    log=None,
) -> dict[str, Any]:
    """Run every (selected) case, score, roll up, write report + per-case records. Returns the report dict."""
    import sys

    from pydantic_ai.messages import ModelMessagesTypeAdapter

    log = log or sys.stderr
    gold = gold or load_gold()
    cases = [c for c in gold.cases if not case_ids or c.id in case_ids]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = out_dir or (RUNS_EVALS / f"{mode}-{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)
    con = T.open_sandboxed(db_path)
    # gold NCTs are frozen against the full dump; score recall only over trials the index under evaluation contains
    db_ncts = {r[0] for r in con.execute("SELECT nct_id FROM studies").fetchall()}
    gold_ncts_absent: dict[str, int] = {}
    results: list[CheckResult] = []
    outcomes: dict[str, dict[str, Any]] = {}
    entity_pool, nct_pool = Pooled(), Pooled()
    n_unadjudicated_set_cases = 0
    replay_mismatch = 0
    for case in cases:
        case_model = model
        if mode == "replay":
            rec_path = (replay_dir or RUNS_EVALS) / f"{case.id}.json"
            if not rec_path.exists():
                print(f"  {case.id}: no recorded transcript at {rec_path} — skipped", file=log)
                continue
            rec = json.loads(rec_path.read_text())
            if not rec.get("messages"):
                print(
                    f"  {case.id}: recorded run had no transcript (live failure: {str(rec.get('error'))[:60]}) — skipped",
                    file=log,
                )
                continue
            turns = [
                m for m in ModelMessagesTypeAdapter.validate_python(rec["messages"]) if m.kind == "response"
            ]
            case_model = transcript_model(turns)
        print(f"  {case.id} [{case.archetype}/{case.check}] {case.question[:70]}…", file=log, flush=True)
        try:
            oc = asyncio.run(run_case(db_path, case, case_model))
        except Exception as e:  # noqa: BLE001
            oc = {
                "case_id": case.id,
                "answer": None,
                "error": f"{type(e).__name__}: {e}",
                "trace": [],
                "gate": None,
            }
        if mode == "replay":
            replayed = oc.get("answer")
            recorded = json.loads(((replay_dir or RUNS_EVALS) / f"{case.id}.json").read_text()).get("answer")
            if (replayed or {}).get("answer_md") != (recorded or {}).get("answer_md") or oc.get("error"):
                replay_mismatch += 1
        outcomes[case.id] = oc
        results += score_case(case, oc, con)
        if oc.get("answer") and case.check in ("entity_set", "nct_set"):
            if not case.adjudicated or case.borderline:
                n_unadjudicated_set_cases += 1
            elif case.check == "entity_set":
                entity_pool.add(
                    case.id,
                    frozenset(answer_entity_surface(oc["answer"])),
                    frozenset(_norm(e) for e in case.expected.entities),
                )
            else:
                present = frozenset(n for n in case.expected.ncts if n in db_ncts)
                if len(present) < len(case.expected.ncts):
                    gold_ncts_absent[case.id] = len(case.expected.ncts) - len(present)
                nct_pool.add(case.id, frozenset(answer_nct_surface(oc["answer"])), present)
        # persist per-case record (replay fixture)
        rec_out = {k: v for k, v in oc.items() if k != "messages"}
        if oc.get("messages") is not None:
            rec_out["messages"] = ModelMessagesTypeAdapter.dump_python(oc["messages"], mode="json")
        (out_dir / f"{case.id}.json").write_text(json.dumps(rec_out, indent=1, default=str))
    con.close()

    # ---- pooled set metrics (thresholds only above the pooled-denominator gate)
    thr = gold.metadata.thresholds
    for pool, prefix, _thresholds in (
        (nct_pool, "nct_set", {"precision": thr.nct_precision, "recall": thr.nct_recall}),
        (entity_pool, "entity_set", {"f1": thr.entity_f1}),
    ):
        if pool.cases:
            gated = pool.n_gold >= thr.min_pooled_gold_items
            for r in pool.results(prefix, "pooled"):
                if not gated and r.role is Role.OBJ:
                    r.role = Role.DIAG
                    r.detail.append(
                        {
                            "note": f"pooled gold {pool.n_gold} < {thr.min_pooled_gold_items}: reported, not gated"
                        }
                    )
                results.append(r)
    results.append(
        CheckResult(
            metric="unadjudicated_set_cases",
            role=Role.DIAG,
            section="pooled",
            value=n_unadjudicated_set_cases,
        )
    )
    results.append(
        CheckResult(
            metric="replay_mismatch_count",
            role=Role.FLOOR if mode == "replay" else Role.DIAG,
            section="replay",
            value=replay_mismatch,
        )
    )
    # ---- DIAG: usage / latency / views touched
    usages = [o.get("usage") or {} for o in outcomes.values()]
    results.append(
        CheckResult(
            metric="total_input_tokens",
            role=Role.DIAG,
            section="usage",
            value=sum(u.get("input_tokens") or 0 for u in usages),
        )
    )
    results.append(
        CheckResult(
            metric="total_output_tokens",
            role=Role.DIAG,
            section="usage",
            value=sum(u.get("output_tokens") or 0 for u in usages),
        )
    )
    results.append(
        CheckResult(
            metric="mean_latency_ms",
            role=Role.DIAG,
            section="usage",
            value=round(sum(o.get("elapsed_ms") or 0 for o in outcomes.values()) / max(len(outcomes), 1)),
        )
    )
    multi = sum(
        1
        for o in outcomes.values()
        if len({_view_of(t) for t in o.get("trace", []) if t.get("tool") == "run_sql"} - {None}) >= 2
    )
    results.append(
        CheckResult(
            metric="pct_answers_touching_2plus_views",
            role=Role.DIAG,
            section="usage",
            value=round(100 * multi / max(len(outcomes), 1), 1),
        )
    )

    rollup = roll_up(
        [r for r in results if not r.section.startswith("case:") or not _is_borderline(r.section, gold)]
    )
    report = {
        "mode": mode,
        "stamp": stamp,
        "db": db_path,
        "gold_ncts_absent_from_db": gold_ncts_absent,
        "n_cases": len(outcomes),
        "passed": rollup.passed,
        "obj_score": round(rollup.obj_score, 4),
        "floor_breaches": rollup.floor_breaches,
        "floor_breach_cases": sorted(
            {
                d["case"]
                for r in results
                if r.role is Role.FLOOR and r.value > 0
                for d in r.detail
                if "case" in d
            }
        ),
        "case_scores": {
            cid: next(
                (r.value for r in results if r.section == f"case:{cid}" and r.metric == "case_score"), None
            )
            for cid in outcomes
        },
        "results": [r.model_dump() for r in results],
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    (out_dir / "report.md").write_text(render_report(report, gold))
    print(
        f"eval {mode}: passed={report['passed']} obj={report['obj_score']} floors={report['floor_breaches']} → {out_dir}",
        file=log,
    )
    return report


def _view_of(t: dict) -> str | None:
    m = re.search(r"\bFROM\s+([a-z_]+)", t.get("input", {}).get("sql", ""), re.IGNORECASE)
    return m.group(1).lower() if m else None


def _is_borderline(section: str, gold: Gold) -> bool:
    cid = section.split(":", 1)[1]
    return any(c.id == cid and c.borderline for c in gold.cases)


def render_report(report: dict[str, Any], gold: Gold) -> str:
    lines = [
        f"# eval report — {report['mode']} {report['stamp']}",
        "",
        f"**passed:** {report['passed']} · **objective:** {report['obj_score']} · **FLOOR breaches:** {report['floor_breaches'] or 'none'}"
        + (f" (cases {', '.join(report['floor_breach_cases'])})" if report["floor_breach_cases"] else ""),
        "",
        "| case | archetype | check | score | adjudicated | borderline |",
        "|---|---|---|---|---|---|",
    ]
    by_id = {c.id: c for c in gold.cases}
    for cid, sc in report["case_scores"].items():
        c = by_id[cid]
        lines.append(
            f"| {cid} | {c.archetype} | {c.check} | {sc if sc is not None else 'FAILED'} | {c.adjudicated} | {c.borderline} |"
        )
    lines += ["", "| metric | role | value | denominator | section |", "|---|---|---|---|---|"]
    for r in report["results"]:
        if not r["section"].startswith("case:") or r["metric"] in ("case_score",) or r["value"]:
            if r["metric"] in ("answer_ncts", "answer_entities"):
                continue
            lines.append(
                f"| {r['metric']} | {r['role']} | {r['value']} | {r['denominator'] if r['denominator'] is not None else ''} | {r['section']} |"
            )
    return "\n".join(lines) + "\n"
