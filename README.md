# ct-landscape

A ClinicalTrials.gov landscape-question agent: one DuckDB index, a small tool-using agent, and a chat UI whose every answer is verified against retrieved evidence and traceable to the source trials.

Brief: `argon-brief.md`. Design specification: `ct-landscape-agent-design.md`. Build progress: `TASKS.md`.

## Quickstart (Phase 0 — scaffold only so far)

```bash
uv sync
uv run pytest
uv run ctl --help
```

Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` for live chat and enrichment; build, SQL console, and offline eval replay work without it.

*(Run instructions, completeness funnel, eval results, tradeoffs, limitations, and AI-usage notes are added phase by phase.)*
