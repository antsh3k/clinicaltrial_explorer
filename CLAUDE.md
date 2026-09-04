# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`ct-landscape`: an agent that answers landscape-level questions about ClinicalTrials.gov (drugs in development for an indication, most-advanced programs, most-active companies, MoA/targets, trials by mechanism, biomarkers/subgroups, combination partners). It is a take-home for the brief in `argon-brief.md`.

**The specification is `ct-landscape-agent-design.md`. Read the relevant section before touching a phase.** It is self-contained and prescriptive: schema (§4), deterministic pipeline (§5), MoA waterfall (§6), agent/API/UI (§7), evals (§8), build phases (§11), lexicon seeds and code sketches (App. B). When code and spec disagree, the spec wins unless `TASKS.md` records a deliberate deviation.

**`TASKS.md` is the resumable build checklist.** Work proceeds phase by phase; each contained task is committed and pushed straight to `main` (single developer). Tick items off and note deviations there so an interrupted session can resume.

## Commands

```bash
uv sync                          # create/refresh .venv (Python 3.12 pinned in .python-version)
uv run pytest                    # full offline test suite (never hits network or an LLM)
uv run pytest tests/test_x.py -k name   # one test
uv run ruff check . && uv run ruff format .
uv run ctl --help                # ops CLI: build / enrich / serve / eval / sql
uv run ctl build --demo          # ~1 min: data/fixtures/demo.zip → data/ctg_demo.duckdb (raw + entities + views + funnel)
uv run ctl build                 # full corpus from data/raw/ctg-studies.json.zip → data/ctg.duckdb (~8 min)
uv run ctl build --skip-ingest   # re-run normalize + views only (lexicon / views.sql edits) on an ingested DB
uv run ctl sql "SELECT ..." [--db data/ctg_demo.duckdb] [--csv]   # sandboxed read-only console
uv run ctl serve                 # FastAPI + chat UI on :8000 (Phase 5+; needs ANTHROPIC_API_KEY in .env)
```

Iterating on normalization: edit a YAML under `lexicons/` or a `normalize/*.py`, run the unit tests, then `ctl build --demo`
and read the census lines + funnel; `contested_aliases`, `condition_denoised`, and `build_meta.normalize_census`
(per-gate counts) are the diagnostic tables. DuckDB gotcha: `~` is a FULL-string regex match; use `regexp_matches()`.

`ctl` is ops-only; the product interface is the web chat UI served by FastAPI.

## Architecture (the big picture)

Batch pipeline → one DuckDB file → small typed agent → FastAPI + static chat page. Three strictly ordered data layers:

1. **Raw tables** (`ingest.py`): the dump verbatim, all ~601k studies. Scope filters never live here. Only single-field pure derivations are allowed (`phase_norm`, `*_parsed` dates).
2. **Entity/edge tables** (`normalize/`): deterministic, idempotent, zero LLM calls. Interventions → assets via cleaner → whole-label noise gates → dedup-key router (combo / biologic-shaped / fixed-point salt-strip) → `otherNames` alias merge with a contested-alias veto. Arm structure gives roles (`subject` / `comparator` / `unknown`, subject-first, three-valued) and combos. Conditions keep two surfaces (`mesh_leaf`, `listed`); MeSH ancestors are quarantined to rollups. Sponsors → companies via suffix-pop plus a curated alias file. **No fuzzy matching anywhere.**
3. **Views** (`views.sql`): every landscape metric defined exactly once (`v_programs`, `v_sponsor_activity`, `v_moa`, `v_combos`, `v_population_landscape`, `v_trial_card`, …). Counting views join `v_trial_conditions_primary` (one condition surface per trial) so nothing is double counted. Build fails if any view is empty.

MoA/targets (`enrich/`) fill through a provenance waterfall `chembl` > `nlm_class` > `llm`; nothing overwrites a higher tier. The LLM tier is abstain-first, batched, checkpointed to a shipped JSONL, and is the one degradable component.

The agent (`agent/`, Pydantic AI) has three read-only tools (`resolve_entity`, sandboxed `run_sql`, `get_trial`) and a structured `Answer` output type. The **output validator is a fail-closed grounding gate**: every NCT and entity in an answer must have appeared in a tool result this conversation. `answer_question()` is an interface-agnostic event generator consumed by both the SSE API and the eval harness.

Evals (`evals/`): gold YAML with human-adjudicated expected sets; metrics carry a role (`FLOOR` = any breach fails, `OBJ` = averaged quality, `DIAG` = inert); set metrics are pooled, never macro-averaged; offline transcript replay via `FunctionModel` gates CI.

## Invariants to preserve

- Missing ≠ zero. Absent fields become `unknown`/NULL and are counted, never dropped silently; every pipeline drop is counted by reason (census → `build_meta`).
- No enumeration caps in views. Trial-derived stage is capped at Phase 3 semantically; a Phase 4 trial is never evidence of approval.
- `snapshot_date` comes from the data (`max(last_update_date_parsed)`), never the wall clock.
- Never persist a lossy child→parent condition rewrite. Never use substring containment as equality for companies.
- `run_sql` sandbox has four layers (read-only connection, `enable_external_access=false` + `lock_configuration=true`, memory/time limits, single SELECT/WITH statement). Open a fresh read-only DuckDB connection per request; never share one across concurrent requests.
- Tests never call live APIs or LLMs: use `data/fixtures/mini.zip`, hand-built fixtures, `agent.override(model=TestModel())`, and recorded transcripts via `FunctionModel`.
- Enrichment and the agent run on Haiku 4.5 / Sonnet 5 class models per §6.6; a `refusal` stop reason is a settled abstain.
- Every deliberate deviation from the spec is recorded in `TASKS.md`; prompts used with coding agents are logged in `PROMPTS.md` (a brief deliverable).

## Notes on the environment

- Installed `pydantic-ai` is 2.x, much newer than the version the spec's snippets assumed. The names the spec relies on (`Agent`, `ModelRetry`, `RunContext`, `ToolOutput`, `UsageLimits`, `ModelSettings`, `TestModel`, `FunctionModel`, `run_stream_events`, `output_validator`) all exist; check signatures against the installed package rather than the spec snippets.
- The full dump (`data/raw/ctg-studies.json.zip`, ~2.7 GB, 601,694 studies on the 2026-09-04 snapshot) and `*.duckdb` are gitignored. `data/fixtures/*.zip` and `data/enrichment/*.jsonl` are shipped in-repo.
