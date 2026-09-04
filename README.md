# ct-landscape

A ClinicalTrials.gov landscape-question agent: one DuckDB index, a small tool-using agent, and a chat UI whose every answer is verified against retrieved evidence and traceable to the source trials.

Brief: `argon-brief.md`. Design specification: `ct-landscape-agent-design.md`. Build progress: `TASKS.md`.

## Quickstart (through Phase 1: raw index)

```bash
uv sync
uv run pytest                      # offline: hand-built records + data/fixtures/mini.zip
uv run ctl build --demo            # ~2 s: data/fixtures/demo.zip (15k studies) → data/ctg_demo.duckdb
uv run ctl sql "SELECT study_type, count(*) FROM studies GROUP BY 1" --db data/ctg_demo.duckdb

# full corpus (~2.7 GB download, ~70 s ingest on a laptop)
uv run ctl fetch                   # or: empty search on clinicaltrials.gov → Download → JSON zip → data/raw/ctg-studies.json.zip
uv run ctl build                   # → data/ctg.duckdb
```

The demo slice holds every trial for the gold-set indications (Erdheim-Chester, geographic atrophy, multiple myeloma, IPF, NSCLC, RCC) plus a random sample, with never-ingested modules pruned; `data/fixtures/*.manifest.json` records the selection.

Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` for live chat and enrichment; build, SQL console, and offline eval replay work without it.

*(Run instructions, completeness funnel, eval results, tradeoffs, limitations, and AI-usage notes are added phase by phase.)*
