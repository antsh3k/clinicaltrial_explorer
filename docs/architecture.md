# Architecture

How `ct-landscape` turns the ClinicalTrials.gov dump into an index the agent can answer landscape questions over, and the choices behind it. The [README](../README.md) covers setup, what it answers and the UI; [`evaluation.md`](evaluation.md) covers how accuracy is verified. The design specification (`../ct-landscape-agent-design.md`) is the authority when this document and the code disagree.

Anti-goals, deliberately: no embeddings, no BM25, no graph database, no frontend build chain, no orchestration framework beyond Pydantic AI's typed primitives. Landscape questions are aggregations over a clean index, not lookups over prose.

---

## The pipeline

```
ctg-studies.json.zip ─ fetch → ingest ──raw tables (verbatim, all 601,694 studies)──→ normalize/ ──entity + edge tables──→ enrich/ ──MoA tiers──┐
                                   census                                     census (per-gate drop counts)              census (join)          │
                                                     ctg.duckdb  ←──────────────────── views.sql (every metric defined once) ←────────────────┘
                                                          │
                              agent/: Pydantic AI Agent — resolve_entity · run_sql · get_trial → submit_answer(Answer) → output_validator = grounding gate
                                                          │
                              api/: FastAPI — POST ask (SSE) · answers (permalink) · trials · entities/resolve · sql · meta;   web/: one static page
                                                          │
                              evals/: gold.yaml → harness → FLOOR / OBJ / DIAG report (+ offline replay gate, mutation suite)
```

### The ontology: a relational star schema + controlled vocabularies + definition-of-record views

Three strictly ordered layers; scope filters (interventional, industry, drug/biologic) live **only** in the views, so every scoping decision is reversible and countable.

1. **Raw** (`ingest.py`): the dump verbatim — `studies`, `study_conditions`, `interventions`, `intervention_other_names`, `arms`, `arm_interventions`, `sponsors`, `mesh_terms` (condition and intervention MeSH leaves **and** ancestors). The only derived columns are single-field pure functions: `phase_norm` (combined phases round up) and month-padded `*_parsed` dates with a precision flag. `snapshot_date` is the max last-update date **in the data**, never the wall clock.
2. **Entities and edges** (`normalize/`, deterministic, zero LLM calls):
   - **Assets.** Intervention names → registry-specific cleaning → **whole-label noise gates** (placebo/sham/vehicle, standard-of-care, class labels such as "corticosteroids" or "PD-1", metadata cues; every rejection counted by gate) → a **dedup-key router** that picks one of three key shapes: combination names split on `/ + and with` (components kept as edges), biologic-shaped names keep their Greek qualifier and biosimilar suffix (epoetin alfa ≠ epoetin beta; trastuzumab ≠ trastuzumab-dkst), everything else runs a fixed-point loop of salt / dose-form / device / edge-qualifier strips with an electrolyte guard ("potassium chloride" stays whole). The regimen split ("lenalidomide dexamethasone" → two known single-token assets) runs only after the salt / dose-form / device strips and never accepts a form word as a member, so "abiraterone acetate" and "nicotine gum" stay one drug. Then `otherNames` — the trial itself saying "MK-3475 = pembrolizumab = KEYTRUDA" — merge clusters through a union-find with three guards: an alias claimed by several clusters is **vetoed** unless one claimant dominates (≥5 trials and ≥10× every other); uniting two existing clusters needs the alias asserted in ≥2 trials (one trial's `otherNames` often enumerate alternatives); and on an intervention listing ≥4 `otherNames`, a name no other trial asserts attaches only when it is code-shaped or token-related to the intervention (pasted product lists). Every decision is written to `contested_aliases` with its resolution. A small curated synonym file (`lexicons/asset_synonyms.yaml`, ~45 INN ⟷ code ⟷ brand groups for the eval indications) is applied before the `otherNames` pass so a fixture with one trial per name still unifies BI 1015550 with nerandomilast; registry evidence always keeps the provenance label. No fuzzy matching anywhere.
   - **Arm roles.** From `armGroups[]` + `armGroupLabels`: an asset is `subject` if it sits in any EXPERIMENTAL arm, `comparator` if only in comparator-type arms, else `unknown` (OTHER-typed arms, arm-less records) — three-valued and retained, never read as False. `in_all_arms` (the background-therapy signal) is defined only on ≥2-arm trials.
   - **Conditions.** Two surfaces share one key column: MeSH leaf ids from the dump (`mesh_leaf`) and an order-preserving fold of the listed strings (`listed`), with a denoise census (healthy volunteers, quality-of-life-only, biomarker-only, device-only). Counting views read **one surface per trial** (`v_trial_conditions_primary`), so a trial listing "NSCLC" and carrying D002289 is counted once. MeSH ancestors are quarantined to rollups: a static heading → therapeutic-area table with first-present-wins priority, and an explicit `Unclassified` bucket for listed-only conditions. A child condition is never rewritten to its parent.
   - **Companies.** Suffix-pop normalisation (legal forms, industry words), ~30 curated alias groups with dated acquisitions (Celgene → BMS 2019, Seagen → Pfizer 2023 …), and the registry's own self-declared parents ("Genzyme, a Sanofi Company"). Never substring containment. `agency_class` is kept verbatim; lead vs collaborator stay distinct roles; ownership is a disclosed limitation, with `v_asset_sponsors.originator_proxy` (earliest industry lead) as a queryable proxy.
   - **Populations.** A typed lexicon (`lexicons/populations.yaml`, ~250 entries) over title + conditions + eligibility: biomarkers and five subgroup kinds, each mention stored with its evidence line. Inclusion vs exclusion is not parsed (v1 limitation, stated in the agent's system prompt).
3. **Views** (`views.sql`): every landscape metric defined exactly once, with no enumeration caps (`v_programs.nct_ids` is the full list). The build fails if any view is empty.

**Mechanism waterfall** (`enrich/`, provenance-labeled, nothing overwrites a higher tier): (1) **ChEMBL** curated mechanisms + gene-symbol targets (CC BY-SA 3.0, EMBL-EBI; the derived `data/enrichment/chembl_moa.jsonl` ships in-repo, share-alike), joined by **exact** fold of our aliases against ChEMBL names and vetoed only on mechanism ambiguity — ChEMBL never merges or creates assets; (2) a **curated tier** (`lexicons/curated_moa.yaml`): hand-written, cited, gene-level mechanisms for ~35 pipeline assets that ChEMBL and NLM do not carry yet (the IPF PDE4B / αvβ6 integrin / LPA1 agents, the KRAS G12C class, the geographic-atrophy complement agents, the myeloma bispecifics and CAR-Ts), resolved by alias at load so one file serves every index; (3) the dump's own NLM pharmacologic classes (`interventionBrowseModule.ancestors`), attached only when the intervention MeSH leaf keys to the asset's own alias; (4) an abstain-first **LLM tier** (Haiku 4.5 via the Batches API, $35 hard ceiling, append-only checkpoint, `refusal` = settled abstain) for the code-named tail — piloted on 300 assets (shipped; see [`evaluation.md`](evaluation.md)), bulk pass deliberately not run.

### Choices the brief left open, and why

| Decision | Rejected alternative | Why |
|---|---|---|
| DuckDB, one file, in-process, read-only at serve time | Postgres (ops burden), SQLite (weak aggregations) | analytical joins + GROUP BY over millions of rows, zero setup, sandboxable connections |
| Star schema + vocabularies + definition-of-record views as the "ontology" | graph DB, embeddings | every archetype is a 1–2-hop join; metrics defined once; SQL is what an LLM writes reliably |
| Raw layer keeps all 601k studies; scope in views | scope at ingest | reversible, auditable, countable |
| Drug identity from the registry's own structure (otherNames + arm tables) with a contested-alias veto + dominance rule | fuzzy matching; RxNorm/PubChem/LLM cascades | single-source corpus; fuzzy matching is the largest over-merge risk per line of code |
| Conditions: in-dump MeSH leaves + folded strings; ancestors only for rollups | UMLS/ICD crosswalks; LLM disease resolution | license-free, offline; ancestors in precise gates manufacture false positives |
| Three-valued arm roles | binary comparator flag | single-arm trials label everything OTHER; absence is not evidence of comparator |
| ChEMBL as lookup, never as alias evidence | ChEMBL synonyms feeding merges | external alias accretion into a global namespace is where cross-source over-merges come from |
| Agent-written SQL over documented views, in a four-layer sandbox | a fixed per-question tool catalogue | open question space; the schema card + gate + FLOOR evals carry the risk |
| Fail-closed grounding gate at the output boundary, on NCTs **and** entities | fail-open | evidence is a fully observed local index; "exists in the index" is not "was retrieved" |
| Answers persisted to JSON files | writing answers into DuckDB | the DB stays pristine and single-writer; the answer store doubles as replay fixtures |

---

## Completeness funnel (emitted by `ctl build` on the full corpus, snapshot 2026-09-04)

```
601,694 studies ingested (= 601,694 zip members; 0 parse failures)
  → 459,233 interventional → 225,018 drug/bio interventional → 132,164 industry-lead → 90,304 in scope (industry ∩ interventional ∩ drug/bio)
interventions: 470,008 drug/bio names → −76,013 gated {placebo/sham prefix 42,191 · exact noise 11,203 · class/procedure regex 9,204 ·
               placebo remnant 3,879 · too long 3,741 · metadata cue 2,015 · either/or arms 1,317 · qualifiers-only 531 · …}
  → 393,995 keyed (83.8%) → 81,128 assets + 18,695 combination assets; 3,447 merged via otherNames (16,908 single-trial
    merges blocked; 2,740 single-trial names on long otherNames lists not attached); 87 curated synonym merges;
    936 aliases assigned by dominance; 10,065 contested aliases vetoed (logged, never applied)
  → 41,277 in-scope assets → mechanism-labeled: chembl 7,286 · curated 36 · nlm_class 1,260 · llm 267 (pilot; 28 abstained)
    → 14.7% of in-scope assets carry ≥1 mechanism label = 56.2% of in-scope trial×asset rows (the head of the distribution)
conditions: 78.9% of trials carry ≥1 MeSH leaf; 108,071 listed-only (→ area "Unclassified", never dropped);
            denoise drops {healthy volunteers 18,075 · device/procedure 14,332 · behaviour/QoL 13,796 · biomarker-only 2,810 · too short 307}
arms: 92.3% of drug trials have arms; 87.2% of (trial, asset) roles decidable {subject 277,466 · comparator 65,947 · unknown 50,582}
populations: % of trials with ≥1 typed mention — demographic 57.6 · disease stage 49.5 · severity 38.2 · biomarker 14.0 · line of therapy 8.8 · prior therapy 7.1
```

Every number is a claim the eval can audit; the UI's coverage footer restates the load-bearing ones on every answer, and MoA answers additionally state their own labeled fraction ("N of M in-scope assets for this indication carry a mechanism label", computed by SQL).

---

## Where it performs well, where it performs poorly

**Well.** Asset identity for the head of the distribution (brand/code/INN unification through the registry's own `otherNames`; MK-3475 → pembrolizumab across 2,428 trials); comparator exclusion and combination detection from arm structure (87.2% of roles decidable); condition matching by construction with no double counting; company aliasing including dated acquisitions and self-declared parents; everything is countable and sub-second (a full-scan aggregate over `v_programs` runs in ~0.3 s).

**Poorly / honestly limited.**
- **Mechanism coverage is head-heavy**: 14.7% of in-scope assets (56.2% of trial×asset rows) carry a ChEMBL / curated / NLM label; the code-named tail is exactly where the LLM tier's 12.5% hard-error rate (pilot) sits, so the bulk pass was not run. MoA answers state their labeled fraction so this is visible per answer, not only per corpus.
- **Asset fragmentation on the tail**: typos, regimen acronyms (CHOP, R-CHOP), qualified phrases and abbreviations stay separate clusters by design (10,065 vetoed aliases, 16,908 single-trial merges blocked). Reading the head of the unlabeled distribution after the pilot found real defects that the tests had not — a 593-trial phantom asset "acetate" (the regimen splitter ran before the salt strip), code names losing their digits to the dose regex ("HRS-5635 Injection" → "hrs"), assay and procedure names typed as drugs ("gene expression analysis", "blood sampling") — all fixed with regression tests. What survives now is small and disclosed: a PET tracer "[11C]acetate" (5 trials), "PCA" as a leading device word (18), a handful of two-trial fragments.
- **Listed-only conditions** (108k trials, 21%) roll up to `Unclassified`; rare/new conditions without MeSH get weaker area rollups.
- **Populations are lexicon-bound** (~250 entries) and do not know inclusion from exclusion; recall is bounded and labeled as such.
- **Sponsor ≠ owner**: licensing and M&A are invisible to the registry beyond the curated file; `originator_proxy` is a proxy.
- **Class-level partners** ("pembrolizumab + chemotherapy") are not represented; only named agents survive the gates.
- **`lead_company_of_most_advanced`** picks the lead of the highest-phase trial, which is often an academic group running a Phase 4 study — correct by definition, easy to misread; the agent is told to say which metric it used.

---

## Precision / recall tradeoffs (the dials)

| Dial | Default | Direction | Visible in |
|---|---|---|---|
| Comparator exclusion from `v_programs` | exclude `comparator`, keep `unknown` | P↑ for "in development"; R risk on OTHER arms mitigated by three-valued retention | `n_unknown_role_trials` column |
| Noise gates on intervention names | whole-label only | P↑; "pembrolizumab immunotherapy" survives | per-gate census (13 gates) |
| Contested-alias veto + dominance rule | veto unless ≥5 trials and ≥10× | P↑ over merge-recall; 936 resolved, 10,065 vetoed | `contested_aliases.resolution` |
| Merge support | cluster-to-cluster merge needs ≥2 asserting trials; long otherNames lists need a code/token link | P↑ (tirofiban ≠ cangrelor); 16,908 merges blocked, 2,740 names not attached | census `n_merges_blocked_single_trial`, `single_trial_on_long_list` |
| Curated synonyms | ~45 INN⟷code⟷brand groups | R↑ on small fixtures, zero corpus risk (curated, exempt from merge support) | `asset_aliases.source = 'curated'`, census `n_curated_synonym_merges` |
| `otherNames` sparsity | accept unmerged synonyms | R↓ on asset unification (lands in NCT-set recall, not precision) | funnel `merged_via_other_names` |
| Combination partners | exclude pairs present in every arm; flag same-mechanism pairs | P↑ (nivolumab stops being pembrolizumab's #2 partner); R↓ on add-on trials over a fixed doublet | `v_combo_partners.same_mechanism`, `has_background` |
| Condition surface | `mesh_leaf` else `listed`; ancestors only for rollups | P↑ (ancestors in precise joins manufacture false positives) | schema-card rule; `v_trial_conditions_primary` |
| "In development" | `program_exists` (ongoing/planned or completed ≤3 y) | between "active readout" (P↑) and "ever" (R↑) | both named columns on `v_trials` |
| ChEMBL join | exact fold; veto on mechanism ambiguity | P↑; 80 skipped, 4,444 shared-molecule lookups allowed | join census in `build_meta` |
| Population lexicon | ~250 curated entries | P high, R bounded | per-kind coverage in the funnel |
| Combined-phase round-up | PHASE2/3 → PHASE3 | mild stage inflation, consistent ordinal | documented; gold case G02 caveat |
| LLM tier (when run) | abstain-first, $35 ceiling, trial-count-ranked | P↑, coverage↓; truncation visible as `n_skipped_over_budget` | batch census |

---

## Repository layout

```
pyproject.toml            uv project; deps: duckdb, pydantic, pydantic-ai-slim[anthropic], fastapi, uvicorn, pyyaml, httpx, anthropic, pyarrow
lexicons/                 YAML seeds: noise_names, non_molecule, salt_dose_suffixes, qualifiers, populations, mesh_areas, company_suffixes, company_aliases,
                          target_aliases, asset_synonyms (curated INN⟷code⟷brand groups), curated_moa (cited gene-level mechanisms, tier 2)
docs/                     UI screenshots · llm_pilot_review.md (the LLM-tier hand-check sheet with verdicts)
data/fixtures/            mini.zip (255 rule-picked messiness cases) · demo.zip (15,484 studies) · manifests
data/enrichment/          chembl_moa.jsonl (shipped, CC BY-SA 3.0) · assets.jsonl (LLM-tier pilot, 300 assets) · assets_agreement.jsonl (ChEMBL agreement sample)
src/ct_landscape/
  fetch.py  ingest.py  db.py  views.sql  funnel.py  cli.py
  normalize/   phases · drug_names · assets · arms · conditions · companies · populations · mechanism_key · build
  enrich/      chembl · load · models · prompts · batch
  agent/       gate · tools · schema_card · agent
  api/         app · store · analytics (evidence-set profiles, entity landscapes, reference check)
  web/         index.html · app.js · styles.css (chat, evidence dashboard, SQL console)
  evals/       checks · gold.py · gold.yaml · harness · mutate · replay
tests/                    offline only: hand-built records, data/fixtures/mini.zip, scripted models
runs/                     answers/ conversations/ evals/ (gitignored; persisted answers double as replay fixtures)
```
