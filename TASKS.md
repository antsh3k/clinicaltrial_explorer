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
- [x] Q1/Q2/Q3/Q7 answerable via `ctl sql` with zero LLM spend (smoke-checked on the demo build 2026-09-04)

## Phase 3 — enrich/ (§6)
- [x] 3a `enrich/chembl.py`: REST fetch of 7,561 mechanisms + 5,954 molecules + 1,518 targets → cached JSON (gitignored); exact-fold join (veto on *mechanism* ambiguity; shared-molecule lookups allowed, counted); census printed; ships `data/enrichment/chembl_moa.jsonl` (CC BY-SA 3.0 attribution) and seeds `targets` (1.6k symbols) + `target_aliases`; `ctl enrich chembl`; loader in `enrich/load.py` so `ctl build` is $0
- [x] 3b `enrich/models.py` (`AssetEnrichment` + `self_consistent`), `prompts.py`, `batch.py` (Anthropic Batches, Haiku 4.5, $35 ceiling, append-only JSONL checkpoint, refusal = abstain, `n_skipped_over_budget`, `--dry-run/--limit/--ceiling`); offline tests for settling/cost/checkpoint/plan
- [x] 3b pilot run (user-approved): 300 assets, $0.21, abstain 10.4%, 0 self-inconsistent, targets unvalidated 23.6%; ChEMBL-agreement sample (50, $0.04): 79.5% agreement excl. abstains, ~8% hard errors; hand-check sheet in `docs/llm_pilot_review.md` (**the 30-row ✓/✗ pass is the user's**)
- [ ] 3b bulk on the remaining ~29k in-scope assets (~$22, ceiling $35) → ship `data/enrichment/assets.jsonl` — awaiting the user's go-ahead after the hand-check (`ctl enrich llm`, then `ctl build --skip-ingest` / `--demo`)

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
- [x] Evidence dashboard (brief deliverable 3, beyond §7.6): `api/analytics.py` + `POST /api/trials/profile` (per-trial index facts for an NCT set) and `GET /api/entities/{kind}/{id}/landscape` (condition / drug / company headline + four breakdowns, each with its SQL); `app.js` evidence-set selector (cited ⊂ in-answer ⊂ retrieved), phase / status / sponsor / MoA-tier / start-year charts with cited overlay and cross-filtering into the trial list, the answer table drawn as a figure (row click → listed NCTs + exact asset-id matches), entity landscape cards, coverage funnel as a figure, "SQL" buttons that re-run any figure in the console; panes scroll independently. Div-based bars, no chart library (keeps the no-build rule). Tests in `tests/test_api_dashboard.py` (every chart's SQL re-runs through the console sandbox; missing ids reported, not dropped)
- [x] Dashboard round 2: biomarker / subgroup breakdown (evidence set + landscape cards; lexicon caveat on the figure), lead-sponsor × phase matrix (evidence set, client-side; condition / drug / company landscapes, server-side `_matrix`), and the **reference check** — each landscape returns definition-of-record rows keyed by exact id and canonical name, and the UI lays them beside the agent's table rows (like-kind equality underlined, differences shown not judged). Profile rows use a lossless JSON conversion, not the agent tool's list-truncating `_shrink`. Tests extended; opened as a PR from the worktree branch

- [x] PER-ENTITY RULE (found by a live run through the dashboard on 2026-09-04): "most active companies in IPF and their most advanced programs" looped one `v_programs` query per company (28 calls) until the 30-turn cap. Fix in the schema card: a house rule (one grouped statement with a top-N CTE + `QUALIFY row_number() OVER (PARTITION BY …)`), a worked Q3+Q2 example, and `tests/test_schema_card.py` running every worked SQL statement through the sandbox on the mini index. Re-run live: 5 tool calls, gate 28/28, 13-row company × program table

## Phase 6 — evals/ (§8)
- [x] `evals/checks.py` (CheckResult/Role/roll_up/set_prf/Pooled + pinned edge cases), `evals/gold.py` loader (`extra="forbid"`, `borderline`, `adjudicated`), `evals/gold.yaml`
- [x] Gold set: 12 core + 2 borderline cases; G01–G07 adjudicated 2026-09-04 by Claude Fable 5.1 from the raw dump (`adjudicated_by`), G05 frozen at 107 NCTs (13 exclusions recorded); **owner sign-off pending**
- [x] `evals/harness.py` driving `answer_question()` (no HTTP); FLOOR/OBJ/DIAG report with id lists (`report.json` + `report.md`); per-case records double as replay fixtures; `--mode replay` replays recorded transcripts through the real agent (replay_mismatch_count FLOOR); `evals/mutate.py`; `ctl eval [--demo] [--mode live|replay] [--case ID]`
- [x] Live runs (Sonnet 5): two full runs on the demo index (~$4 each); run 2 = 13/14 completed, 0 grounding violations, objective 0.90, replay gate 0 mismatches; results + agent-level failure modes in README; G04 (IPF mechanisms) is the documented coverage failure (unlabeled code-named assets → LLM tier)

## Phase 7 — README polish (§11.1 row 7)
- [x] README: 5-minute reviewer path, full build, funnel (real numbers), example Q&As from the views, choices-the-brief-left-open table, where it performs well/poorly, tradeoff dials, evaluation (three layers, FLOOR/OBJ/DIAG, mutation + replay status), observed failure modes, AI-usage section, layout
- [x] README: live eval results + agent-level failure-mode table
- [x] README: UI screenshot of the §7.7 example (`docs/ui-mk3475-rcc.jpg`); UI exercised live in Chrome: timeline, gate badge, table, evidence panel, trace, trial drawer, SQL console, permalink reload; two-turn conversation verified via the API

## Hardening after the review pass (2026-09-04) ✅
- [x] Router: dose regex code-aware + chained tails; regimen split after salt/form strips; CJK marks; tests
- [x] Gates: assay/procedure tails, specimens, bare biology words, rescue/adjuvant/care/medical/ADT class labels
- [x] Aliases: long-list single-trial rule; curated synonym groups (`lexicons/asset_synonyms.yaml`)
- [x] Mechanisms: curated tier (`lexicons/curated_moa.yaml`, 36 assets) between chembl and nlm_class; funnel line
- [x] Q7: backbone pairs excluded, `same_mechanism` flag + PARTNER house rule in the schema card
- [x] README: deliverables map, refreshed funnel/examples/dials/failure modes, second screenshot (two-turn conversation)
- [x] Resolver: conditions reach the MeSH key from abbreviations / lay phrasings / stage-qualified forms (`lexicons/condition_synonyms.yaml` + folded token rung + listed-only sibling note); regression tests; README failure mode

## Needs the user (blocked)
- [x] `ANTHROPIC_API_KEY` in `.env` (done by the user); live chat verified on the §7.7 question; live evals run
- [x] Phase 3b pilot — done: 29,299 in-scope assets lack a curated mechanism; pilot of 300 ≈ $0.23, full tail ≈ $22 (ceiling $35). Awaiting your go-ahead: `ctl enrich llm --limit 300` → hand-check 30 rows → `ctl enrich llm` for the rest → `ctl build --demo` / rebuild to load `data/enrichment/assets.jsonl`
- [ ] Owner sign-off on the Fable-5.1 adjudication of G01–G07 (read each case `note` in `evals/gold.yaml`; veto or amend) and on the 30 pilot verdicts in `docs/llm_pilot_review.md`
- [ ] Re-run `ctl eval --demo` (~$4) so the pooled NCT precision/recall (now gated via G05) and the G01/G06 changes are measured, and to re-record the replay fixtures against the hardened index (12/14 replay clean today; G07 and G05b diverge because the index got stricter — cabozantinib no longer a pembrolizumab partner, RMC-9805 folded into zoldonrasib)
- [x] Chrome instance picked by the user; UI tested (two drawer glitches + a parallel-call spinner fixed)

## Deviations from the spec
- `resolve_entity` (§7.2) for `kind=condition` gained two rungs ahead of the listed-string prefix/contains rungs: `curated` (`lexicons/condition_synonyms.yaml`, folded surface → MeSH id, 99 descriptors verified against the snapshot) and a stronger `tokens` rung (order-insensitive, cancer/carcinoma/neoplasm/tumor folded to one token, stage qualifiers such as "advanced"/"metastatic"/"stage IV" dropped from the query). Both are deterministic lookups, never fuzzy. The file is read only by the resolver — the persisted condition keyspace (§5.3) is untouched; when the MeSH key wins by synonym and the same surface also exists as a listed-only key, that key is returned as a second candidate with a note that the two are never summed. `Candidate.match` gained the value `curated`. Motivation: live-eval G05/G06/G12 — "NSCLC" resolved to the listed-only key `nsclc` (wrong keyspace) and "non-small cell lung cancer" never returned D002289.
- Mechanism waterfall (§6) gained a `curated` tier between `chembl` and `nlm_class`: `lexicons/curated_moa.yaml`, hand-written, cited, gene-level labels for pipeline assets ChEMBL/NLM lack (IPF integrin/LPA1/PDE4B agents, the KRAS G12C class, GA complement agents, MM bispecifics/CAR-Ts). Resolved by alias at load; never overwrites a higher tier.
- Asset identity (§5.1) gained `lexicons/asset_synonyms.yaml`: curated INN ⟷ code ⟷ brand groups united before the otherNames pass and exempt from the ≥2-trial merge rule, so small fixtures keep BI 1015550 = nerandomilast. Members absent from the registry become aliases with source `curated`; registry evidence (name / other_name) always wins the provenance label.
- otherNames on an intervention listing ≥4 of them: an otherName no other trial asserts attaches only when code-shaped or token-related to the intervention name (pasted product lists such as canakinumab → [Ultralente, Velosulin, Tolinase, Tolazamise]); counted as `single_trial_on_long_list`.
- Regimen split (§5.1 step 3) runs AFTER the salt / dose-form / device strips and never accepts a form word as a member ("abiraterone acetate", "nicotine gum" are one drug). The dose regex no longer starts inside a code name ("HRS-5635 Injection") and consumes chained per-unit tails ("mg/m²/day").
- `v_combo_partners` (§4.3 / Q7) drops pairs where BOTH agents sit in every arm (the trial's backbone or an investigator's-choice list, e.g. "SOC immunotherapy: nivolumab, pembrolizumab, …") and exposes `same_mechanism` (identical ChEMBL mechanism, e.g. nivolumab + pembrolizumab in one arm) as a flag the schema card tells the agent to report separately — a flag, not a filter, because trastuzumab + pertuzumab is real.
- Cluster-to-cluster alias merges (§5.1 step 6) require ≥2 asserting trials; the pilot hand-check found single-trial `otherNames` that enumerate alternatives (tirofiban/cangrelor) or regimen members (clofazimine/dapsone). Single assertions still attach brands/codes.
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
- Repo root is `clinicaltrial_explorer/` (not `ct-landscape/`); package name is `ct_landscape` as specified.
- README split (§11.1 row 7 lists funnel, tradeoffs, limitations, eval results and AI-usage as README content): the README now carries only setup, what it answers, the UI walkthrough with screenshots, example questions and a documentation index; the architecture/ontology, choices table, completeness funnel, strengths/limitations, precision/recall dials and repository layout moved to `docs/architecture.md`, the evaluation results and failure modes to `docs/evaluation.md`, and the AI-usage summary to the top of `PROMPTS.md`. Same content, relocated so the README stays a clean entry point (user request, 2026-09-04).
