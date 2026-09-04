# TASKS.md — resumable build checklist

Source of truth for scope: `ct-landscape-agent-design.md` (§ references below). One commit per contained task, pushed to `main`.
Mark items `[x]` when done. Record any deliberate deviation from the spec under **Deviations** at the bottom.

## Phase 0 — Scaffold (§11.1) ✅ target: `pytest` green, `ctl --help`
- [x] uv project: `pyproject.toml`, Python 3.12 pinned, deps synced (duckdb, pydantic, pydantic-ai-slim[anthropic], fastapi, uvicorn, pyyaml, httpx, anthropic; dev: pytest, ruff)
- [x] Package skeleton `src/ct_landscape/{normalize,enrich,agent,api,web,evals}` + `ctl` console script with stub subcommands
- [x] `CLAUDE.md`, `TASKS.md`, `PROMPTS.md`, `.env.example`, gitignore for raw dump / duckdb / runs
- [x] Fixture-builder script `scripts/make_fixtures.py` (mini.zip 213 studies covering every §2.5 case; demo.zip ~17k = all gold-indication trials + 1,500 random)

## Phase 1 — fetch + ingest → raw tables + census (§5 Stage 0–1, §4.1)
- [x] `fetch.py`: internal download URL primary; documented v2 pager fallback (`ctl fetch --pager`); census bytes + n_files
- [x] Acquire the full dump into `data/raw/` (gitignored) — 2.74 GB, 601,694 studies, snapshot 2026-09-04
- [x] `normalize/phases.py`: round-UP rule, NA stays NA, empty → NULL; set-intersection whitelist; tests incl. `test_combined_phase_rounds_up`
- [x] `ingest.py`: zip member → `json.loads` → lean Pydantic boundary models (`extra="ignore"`) → Arrow batches → DuckDB for all §4.1 raw tables; arm join by `armGroupLabels` with `interventionNames` fallback (count both paths); partial-date parsing + `date_precision`; parse failures listed
- [x] `db.py`: schema DDL, `build_meta` writer, `snapshot_date = max(last_update_date_parsed)`, sandboxed read-only connection
- [x] Census printed + written to `build_meta` (n_read, n_loaded, per-module absence counts)
- [x] `scripts/make_fixtures.py` → ship `data/fixtures/mini.zip` and `data/fixtures/demo.zip` (+ manifests); tests assert exact counts on mini.zip
- [x] `ctl build --demo` end-to-end; `ctl sql` read-only console
- [x] Measured full-corpus ingest: 69 s with 13 worker processes (~8.5k studies/s), 0 parse failures

## Phase 2 — normalize/ + views.sql (§5.1–5.5, §4.2–4.3, App. B)
- [x] Lexicon YAMLs under `lexicons/`: `noise_names`, `non_molecule`, `salt_dose_suffixes`, `qualifiers`, `populations` (~250 typed entries), `mesh_areas`, `company_suffixes`, `company_aliases`, `target_aliases`
- [x] `normalize/drug_names.py`: cleaning, whole-label noise gates (per-gate census), dedup-key router (combo / biologic-shape / fixed-point + electrolyte guard + edge-qualifier strip + known-token regimen split), code-shape detection
- [x] `normalize/assets.py` + `build.py`: group by dedup_key, otherNames alias merge (union-find, root-level contested-alias veto), global alias uniqueness, canonical name + brand display; tests `test_placebo_never_an_asset`, `test_mk3475_is_pembrolizumab`, contested veto
- [x] `normalize/arms.py`: roles subject-first three-valued, `in_all_arms` NULL unless ≥2 arms; arm-level + name-level combos in `v_combos`; `test_comparator_not_in_development`
- [x] `normalize/conditions.py`: fold (order-preserving), denoise with reason census + disease-noun KEEP, dual surface (mesh_leaf / listed), area rollup with priority (`Unclassified` bucket); `test_juvenile_condition_not_rewritten`, `test_trial_counted_once_across_condition_surfaces`
- [x] `normalize/companies.py`: suffix-pop loop + curated alias groups (~30 groups, dated acquisitions) + self-declared parent parsing ("X, a Sanofi Company"); never substring equality
- [x] `normalize/populations.py`: typed lexicon regex over title/conditions/eligibility, `evidence_line`; literal-trigger prefilter (~4 ms/study)
- [x] `normalize/mechanism_key.py` (App. B.7); `moa_key` stored as a column on every MoA tier table (no SQL UDF)
- [x] `views.sql`: `v_trials`, `v_trial_conditions_primary`, `v_conditions`, `v_programs` (row_number tie-break by latest_activity), `v_asset_max_phase`, `v_assets`, `v_sponsor_activity`, `v_asset_sponsors`, `v_moa` + `v_moa_best`, `v_moa_trials`, `v_combos` + `v_combo_partners`, `v_population_landscape`, `v_trial_card`; build fails on any empty view
- [x] Census funnel (§8.5) printed by `ctl build` (`funnel.py`), stored in `build_meta.funnel`
- [ ] Q1/Q2/Q3/Q7 answerable via `ctl sql` with zero LLM spend (smoke-check on demo build)

## Phase 3 — enrich/ (§6)
- [x] 3a `enrich/chembl.py`: REST fetch of 7,561 mechanisms + 5,954 molecules + 1,518 targets → cached JSON (gitignored); exact-fold join (veto on *mechanism* ambiguity; shared-molecule lookups allowed, counted); census printed; ships `data/enrichment/chembl_moa.jsonl` (CC BY-SA 3.0 attribution) and seeds `targets` (1.6k symbols) + `target_aliases`; `ctl enrich chembl`; loader in `enrich/load.py` so `ctl build` is $0
- [x] 3b `enrich/models.py` (`AssetEnrichment` + `self_consistent`), `prompts.py`, `batch.py` (Anthropic Batches, Haiku 4.5, $35 ceiling, append-only JSONL checkpoint, refusal = abstain, `n_skipped_over_budget`, `--dry-run/--limit/--ceiling`); offline tests for settling/cost/checkpoint/plan
- [ ] 3b pilot: 300 assets → 30 hand-checked + ChEMBL-agreement sample → measure tokens/abstain/accuracy before bulk (**needs user sign-off on spend**)
- [ ] 3b bulk on un-joined in-scope assets → ship `data/enrichment/assets.jsonl`; load with target validation → `asset_enrichment`; `targets_unvalidated_rate`

## Phase 4 — agent/ (§7.1–7.4)
- [x] `agent/schema_card.py` (system prompt: view catalog, house rules, worked SQL examples; snapshot line from build_meta)
- [x] `agent/tools.py`: `resolve_entity` ladder (exact/alias/prefix/contains; moa fold server-side; condition → MeSH key), sandboxed `run_sql` (four layers, row cap 200, list truncation, ALL ids into `retrieved`), `get_trial`
- [x] `agent/gate.py`: `nct_refs_from_text`, `gate()` pure function; `Answer` model with `table_text()`
- [x] `agent/agent.py`: Pydantic AI 2.x Agent, `Deps`, `ToolOutput(Answer, name="submit_answer")`, output validator, `UsageLimits(request_limit=16, tool_calls_limit=24)`, nonce-fenced tool results; `answer_question()` event generator (history compaction lives in `api/store.py`)
- [x] Tests: `TestModel` smoke, scripted `FunctionModel` (streaming adapter in `evals/replay.py`) driving the real tools + validator, gate retry + exhaustion paths, mutation suite (§8.4) red/green

## Phase 5 — api/ + web/ (§7.5–7.7)
- [x] `api/app.py` + `api/store.py`: conversations, `ask` SSE (tool_call/tool_result/note/gate/answer/error/done), answers permalink, trials, entities resolve, sql console (same sandbox as the agent tool), meta; filesystem answer/conversation store; conversation-scoped gate sets; history compaction (tool payloads digested, 20-turn cap); one in-flight run per conversation; `ctl serve [--demo]`
- [x] `web/index.html`, `app.js`, `styles.css`: live timeline, structured sortable table, citations table with phase/status/sponsor pulled live + both links, gate badge, NCT auto-link (gate's scanner), trace panel (SQL copy / open-in-console) + coverage footer, permalink `#/answers/{id}`, trial-card drawer, SQL tab with the schema card; no build step (marked + DOMPurify pinned from cdnjs)
- [x] `TestClient` tests with a scripted model (no network): SSE event order, gate badge, permalink round-trip, follow-up turn citing a turn-1 NCT, sandbox rejections; `ctl serve --demo` smoke-tested with curl

## Phase 6 — evals/ (§8)
- [x] `evals/checks.py` (CheckResult/Role/roll_up/set_prf/Pooled + pinned edge cases), `evals/gold.py` loader (`extra="forbid"`, `borderline`, `adjudicated`), `evals/gold.yaml`
- [ ] Gold set: 12 core + 2 borderline cases DRAFTED from the spec's case table (`adjudicated: false` except the 5 negative/messiness probes); **expected sets still to be adjudicated by the user by hand** (oracle URL, capture date, raw UI count, `ncts` for G05) — set-based metrics stay DIAG until then
- [x] `evals/harness.py` driving `answer_question()` (no HTTP); FLOOR/OBJ/DIAG report with id lists (`report.json` + `report.md`); per-case records double as replay fixtures; `--mode replay` replays recorded transcripts through the real agent (replay_mismatch_count FLOOR); `evals/mutate.py`; `ctl eval [--demo] [--mode live|replay] [--case ID]`
- [ ] Live runs (Sonnet 5, ~$5) → results + failure modes into README — **needs ANTHROPIC_API_KEY in .env (not present yet)**

## Phase 7 — README polish (§11.1 row 7)
- [ ] 5-minute reviewer path, full build, funnel numbers, example Q&As with evidence, tradeoffs (§9), limitations (§10.3), choices the brief left open (§10.1), works-without-key vs needs-key, AI-usage section (from `PROMPTS.md`)

## Deviations from the spec
- Sandbox: DuckDB shares one database instance per file per process, so the second sandboxed connection finds `lock_configuration` already set; `connect_sandboxed` tolerates that and VERIFIES `enable_external_access=false` instead of re-applying it.
- `UsageLimits.cost_limit` is not set: Pydantic AI's price table did not resolve a cost for `claude-sonnet-5` at build time, so the per-question ceiling is enforced by `request_limit`/`tool_calls_limit` (the spec's stated fallback). Revisit when the price table catches up.
- Pydantic AI 2.x: `run_stream_events` is an async context manager; `FunctionModel` needs a `stream_function` for the streamed path, hence `evals/replay.py`.
- ChEMBL join veto (§6.2) is applied to **mechanism** ambiguity, not synonym sharing: when one ChEMBL molecule names several of our assets (its brands / typos we never merged: progesterone, Prometrium, Endometrin…) each is labeled — a lookup, never a merge; when one alias names several ChEMBL molecules, it is labeled only if their mechanism signatures are identical, else skipped and logged. Counts for both cases are in the join census.
- Route-word / dose-form order: qualifier words are peeled BEFORE the dose-form suffix strip so "Intravenous infusion of ketamine" keys to `ketamine` (the spec's tail-eating dose-form regex alone reduced it to "intravenous"). Names that are only qualifier words are gated (`qualifiers_only`).
- Phase 2 additions beyond the spec text (all deterministic, all counted): edge-qualifier stripping (`lexicons/qualifiers.yaml`) before keying; known-token regimen split (a space-joined name whose every token is a standalone asset with ≥2 trials routes as a combination); self-declared sponsor parents parsed from the registry string; `trial_assets.via` ('name' | 'combo_component') so combo components are counted in `v_programs`; NLM pharmacologic classes attached only when the intervention MeSH leaf keys to one of the asset's aliases and the trial has a single matched leaf.
- Contested-alias **dominance rule** (extension of §5.1 step 6): an alias claimed by ≥2 clusters is still vetoed UNLESS one claimant asserts it in ≥5 trials and ≥10× every other claimant; then it is assigned/merged and logged as `dominance:<asset_id>` in `contested_aliases.resolution` (`vetoed` otherwise). Motivation: on the full corpus every brand alias (Keytruda, Cytoxan, Avastin…) was "contested" by typos, regimen acronyms and qualified phrases that claim it in one trial each.
- `asset_id` = the dedup key of the canonical (non-code) surface (e.g. `pembrolizumab`), not an opaque counter — readable in answers and stable across rebuilds unless the canonical surface changes.
- Dump is 2.74 GB / 601,694 studies (spec's 703 MB / 601,158 was measured on an older snapshot); §2.3 counts reproduce within ~0.2%.
- Fixture zips are *pruned* copies (resultsSection, documentSection, contacts/locations, references, outcomes, detailedDescription, secondaryIdInfos dropped) so demo.zip stays < 50 MB; ingest never reads those modules.
- Raw layer has two extras beyond §4.1: `study_keywords` (conditionsModule.keywords) and `arm_interventions.via` ('label' | 'name') so the arm-join path is auditable per row. Fresh-snapshot finding: every arm link came via `armGroupLabels`; the `interventionNames` fallback was never needed (0 rows).
- `studies.study_first_submit_date` and `primary_purpose` kept as pass-through columns.
- Installed `pydantic-ai` is 2.x (spec snippets assume ~0.4); API names verified present, signatures to be checked at Phase 4.
- Repo root is `clincialtrial_explorer/` (not `ct-landscape/`); package name is `ct_landscape` as specified.
