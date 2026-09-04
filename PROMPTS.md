# PROMPTS.md — how AI coding agents were used

The brief asks for a description of how coding agents / LLMs were used during development. This log is kept from day one (§11.2 of the design spec).

## Before code existed
The specification `ct-landscape-agent-design.md` was produced agentically: explorer agents mined production drug-development normalization and evaluation patterns, a design agent live-probed the ClinicalTrials.gov API (corpus size, download endpoint, field paths, and the finding that `browseBranches` is schema-only and empty in practice), and the human scoped budget, simplicity bar, and interface choice, then adjudicated review findings across several critique rounds.

## Build sessions (Claude Code, one session per phase)

### Session 1 — 2026-09-04 — Phase 0 scaffold
**Prompt (paraphrased):** "I have a brief (`argon-brief.md`) and design instructions (`ct-landscape-agent-design.md`). Follow the instructions to build the app step by step, with a task list so the session can be interrupted and resumed. Commit and push each contained task directly to `main`. Start with `/init` and a uv environment."

**Actions:** read the spec end to end; `/init` → `CLAUDE.md`; `uv` project with Python 3.12 and the §3 dependency list; `ctl` CLI stub; `TASKS.md` checklist keyed to §11 phases; `.env.example`; gitignore for the raw dump, DuckDB files, and run stores. Verified the installed Pydantic AI (2.x) still exposes every object the spec's agent design relies on.

### Session 1 (cont.) — Phase 1 fetch + ingest
**Prompt shape:** same session, continuing per the TASKS.md checklist; the agent was told to commit each contained task to `main`.

**Actions:** probed the internal download endpoint (HTTP 200, `application/zip`; the Range header is ignored, so the fetch is a straight stream); wrote `fetch.py` with the v2 pager fallback; pulled one live record (NCT02142738) to confirm field shapes before writing the boundary models; wrote `ingest.py` (lean Pydantic models → Arrow → DuckDB, multiprocessing by zip-member chunks) and `db.py`; piloted on 10k members, then ran the full corpus (69 s, 0 parse failures); wrote `scripts/make_fixtures.py` to cut `mini.zip` (rule-picked §2.5 messiness cases) and `demo.zip` (all gold-indication trials + random sample, pruned to stay < 50 MB); tests on hand-built records + the mini fixture. One self-inflicted bug caught: `ruff format .` reformatted the Python blocks inside the spec markdown — restored byte-for-byte and excluded `*.md` from ruff.
