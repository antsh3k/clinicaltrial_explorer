# TASKS.md — resumable build checklist

Source of truth for scope: `ct-landscape-agent-design.md` (§ references below). One commit per contained task, pushed to `main`.
Mark items `[x]` when done. Record any deliberate deviation from the spec under **Deviations** at the bottom.

## Phase 0 — Scaffold (§11.1) ✅ target: `pytest` green, `ctl --help`
- [x] uv project: `pyproject.toml`, Python 3.12 pinned, deps synced (duckdb, pydantic, pydantic-ai-slim[anthropic], fastapi, uvicorn, pyyaml, httpx, anthropic; dev: pytest, ruff)
- [x] Package skeleton `src/ct_landscape/{normalize,enrich,agent,api,web,evals}` + `ctl` console script with stub subcommands
- [x] `CLAUDE.md`, `TASKS.md`, `PROMPTS.md`, `.env.example`, gitignore for raw dump / duckdb / runs
- [ ] Fixture-builder script `scripts/make_fixtures.py` (mini.zip ~200 studies covering every §2.5 case; demo.zip ~5–10k) — needs the raw dump first (Phase 1)

## Phase 1 — fetch + ingest → raw tables + census (§5 Stage 0–1, §4.1)
- [ ] `fetch.py`: internal download URL (`/api/int/studies/download?format=json.zip`) primary; documented v2 pager fallback; census bytes + n_files
- [ ] Acquire the full dump into `data/raw/` (gitignored) — the user may need to run the UI download
- [ ] `normalize/phases.py`: round-UP rule, NA stays NA, empty → NULL; set-intersection whitelist; tests incl. `test_combined_phase_rounds_up`
- [ ] `ingest.py`: zip member → `json.loads` → lean Pydantic boundary models (`extra="ignore"`) → DuckDB appenders for all §4.1 raw tables; arm join by `armGroupLabels` with `interventionNames` fallback (count both paths); partial-date parsing + `date_precision`; parse failures listed
- [ ] `db.py`: schema DDL, `build_meta` writer, `snapshot_date = max(last_update_date_parsed)`
- [ ] Census printed + written to `build_meta` (n_read, n_loaded, per-module absence counts)
- [ ] `scripts/make_fixtures.py` → ship `data/fixtures/mini.zip` and `data/fixtures/demo.zip`; tests assert exact counts on mini.zip
- [ ] `ctl build --demo` end-to-end; `ctl sql` read-only console
- [ ] Measure full-corpus ingest time (target ≤ 15 min; parallelize by zip-member chunks if slower)

## Phase 2 — normalize/ + views.sql (§5.1–5.5, §4.2–4.3, App. B)
- [ ] Lexicon YAMLs under `lexicons/`: `noise_names`, `non_molecule`, `salt_dose_suffixes`, `populations`, `mesh_areas`, `company_suffixes`, `company_aliases`, `target_aliases`
- [ ] `normalize/drug_names.py`: cleaning, whole-label noise gates (per-gate census), dedup-key router (combo / biologic-shape / fixed-point + electrolyte guard), code-shape detection, canonical name choice
- [ ] `normalize/build.py` asset assembly: group by dedup_key, otherNames alias merge (path-compressed parent dict) with contested-alias veto, global alias uniqueness; tests `test_placebo_never_an_asset`, `test_mk3475_is_pembrolizumab`, contested veto
- [ ] `normalize/arms.py`: roles subject-first three-valued, `in_all_arms` NULL unless ≥2 arms, arm-level + name-level combos; `test_comparator_not_in_development`
- [ ] `normalize/conditions.py`: fold (order-preserving), denoise with reason census + disease-noun KEEP, dual surface (mesh_leaf / listed), area rollup with priority (`Unclassified` bucket); `test_juvenile_condition_not_rewritten`, `test_trial_counted_once_across_condition_surfaces`
- [ ] `normalize/companies.py`: suffix-pop loop + curated alias groups; never substring equality
- [ ] `normalize/populations.py`: typed lexicon regex over title/conditions/eligibility, `evidence_line`
- [ ] `normalize/mechanism_key.py` (App. B.7)
- [ ] `views.sql`: `v_trials`, `v_trial_conditions_primary`, `v_programs` (arg_max tie-break by latest_activity), `v_asset_max_phase`, `v_sponsor_activity`, `v_asset_sponsors`, `v_moa`, `v_moa_trials`, `v_combos`, `v_population_landscape`, `v_trial_card`; build fails on any empty view
- [ ] Census funnel (§8.5) printed by `ctl build`
- [ ] Q1/Q2/Q3/Q7 answerable via `ctl sql` with zero LLM spend (smoke-check on demo build)

## Phase 3 — enrich/ (§6)
- [ ] 3a `enrich/chembl.py`: REST fetch of mechanisms + molecules (pref_name, synonyms) + target gene symbols → cached JSON; exact-fold join with ambiguity veto; census `n_matched / n_ambiguous_skipped / n_unmatched`; ship `data/enrichment/chembl_moa.jsonl` (CC BY-SA 3.0 attribution); seed `targets` + `target_aliases`
- [ ] 3b `enrich/models.py` (`AssetEnrichment` + `self_consistent`), `prompts.py`, `batch.py` (Anthropic Batches, Haiku 4.5, $35 ceiling, append-only JSONL checkpoint, refusal = abstain, `n_skipped_over_budget`)
- [ ] 3b pilot: 300 assets → 30 hand-checked + ChEMBL-agreement sample → measure tokens/abstain/accuracy before bulk (**needs user sign-off on spend**)
- [ ] 3b bulk on un-joined in-scope assets → ship `data/enrichment/assets.jsonl`; load with target validation → `asset_enrichment`; `targets_unvalidated_rate`

## Phase 4 — agent/ (§7.1–7.4)
- [ ] `agent/schema_card.py` (system prompt: view catalog, house rules, worked SQL examples)
- [ ] `agent/tools.py`: `resolve_entity` ladder (exact/alias/prefix/contains; moa fold server-side; condition → MeSH key), sandboxed `run_sql` (four layers, row cap 200, list truncation, ALL ids into `retrieved`), `get_trial`
- [ ] `agent/gate.py`: `nct_refs_from_text`, `gate()` pure function; `Answer` model with `table_text()`
- [ ] `agent/agent.py`: Pydantic AI Agent, `Deps`, `ToolOutput(Answer, name="submit_answer")`, output validator, `UsageLimits`, history compaction; `answer_question()` event generator
- [ ] Tests: `TestModel` smoke, `FunctionModel` replay of recorded transcripts, mutation mini-suite (§8.4) red/green

## Phase 5 — api/ + web/ (§7.5–7.7)
- [ ] `api/app.py`, `routes.py`, `events.py`: conversations, `ask` SSE, answers permalink, trials, entities resolve, sql console, meta; filesystem answer/conversation store
- [ ] `web/index.html`, `app.js`, `styles.css`: live timeline, structured table, citations table with both links, gate badge, NCT auto-link, trace panel + coverage footer, permalink, SQL tab
- [ ] `TestClient` tests with `agent.override(model=TestModel())`; §7.7 example renders end-to-end

## Phase 6 — evals/ (§8)
- [ ] `evals/checks.py` (CheckResult/Role/roll_up/set_prf + pinned edge cases), `gold.yaml` loader (`extra="forbid"`, `borderline`)
- [ ] Gold set: 12 core cases + 2 borderline; **expected sets adjudicated by the user by hand** (oracle URL, capture date, raw UI count)
- [ ] `evals/harness.py` driving `answer_question()`; FLOOR/OBJ/DIAG report with id lists; `evals/mutate.py`
- [ ] Live runs (Sonnet 5, ~$5) → results + failure modes into README

## Phase 7 — README polish (§11.1 row 7)
- [ ] 5-minute reviewer path, full build, funnel numbers, example Q&As with evidence, tradeoffs (§9), limitations (§10.3), choices the brief left open (§10.1), works-without-key vs needs-key, AI-usage section (from `PROMPTS.md`)

## Deviations from the spec
- Installed `pydantic-ai` is 2.x (spec snippets assume ~0.4); API names verified present, signatures to be checked at Phase 4.
- Repo root is `clincialtrial_explorer/` (not `ct-landscape/`); package name is `ct_landscape` as specified.
