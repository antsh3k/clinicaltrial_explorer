# ct-landscape

A ClinicalTrials.gov landscape-question agent: one DuckDB index, a small tool-using agent, and a chat UI whose every answer is verified against retrieved evidence and traceable to the source trials.

Brief: `argon-brief.md`. Design specification: `ct-landscape-agent-design.md`. Build progress: `TASKS.md`.

## Quickstart (through Phase 3a: index + entities + views + ChEMBL mechanisms)

```bash
uv sync
uv run pytest                      # offline: hand-built records + data/fixtures/mini.zip
uv run ctl build --demo            # ~1 min: data/fixtures/demo.zip (15k studies) → data/ctg_demo.duckdb (raw → entities → views → funnel)
uv run ctl sql "SELECT asset_id, max_phase_active, n_active_trials FROM v_programs WHERE condition_key='D002292' ORDER BY 2 DESC NULLS LAST, 3 DESC LIMIT 10" --db data/ctg_demo.duckdb
uv run ctl enrich chembl --db data/ctg_demo.duckdb   # optional: re-derive the ChEMBL tier live (the shipped JSONL is loaded by `ctl build` at $0)

# full corpus (~2.7 GB download, ~70 s ingest on a laptop)
uv run ctl fetch                   # or: empty search on clinicaltrials.gov → Download → JSON zip → data/raw/ctg-studies.json.zip
uv run ctl build                   # → data/ctg.duckdb (~8 min: 70 s ingest + ~6 min normalize on 13 cores)
```

Every landscape metric is a named view in `src/ct_landscape/views.sql` (`v_programs`, `v_sponsor_activity`, `v_combo_partners`, `v_moa_trials`, `v_population_landscape`, …); `ctl sql` runs any read-only query against them through the same sandboxed connection the agent uses.

The demo slice holds every trial for the gold-set indications (Erdheim-Chester, geographic atrophy, multiple myeloma, IPF, NSCLC, RCC) plus a random sample, with never-ingested modules pruned; `data/fixtures/*.manifest.json` records the selection.

Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` for live chat and enrichment; build, SQL console, and offline eval replay work without it.

*(Run instructions, completeness funnel, eval results, tradeoffs, limitations, and AI-usage notes are added phase by phase.)*
