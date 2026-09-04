"""`ctl` — operations CLI (build / enrich / serve / eval / sql).

This is the ops surface only; the product interface is the web chat UI (§7).
Subcommands are registered here and implemented phase by phase (see TASKS.md).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_ZIP = Path("data/raw/ctg-studies.json.zip")
DEMO_ZIP = Path("data/fixtures/demo.zip")
DEFAULT_DB = Path("data/ctg.duckdb")
DEMO_DB = Path("data/ctg_demo.duckdb")


def _not_implemented(phase: str):
    def run(_args: argparse.Namespace) -> int:
        print(f"not implemented yet — see TASKS.md ({phase})", file=sys.stderr)
        return 2

    return run


def cmd_build(args: argparse.Namespace) -> int:
    from ct_landscape.db import connect
    from ct_landscape.ingest import ingest

    zip_path = Path(args.zip) if args.zip else (DEMO_ZIP if args.demo else DEFAULT_ZIP)
    db_path = Path(args.db) if args.db else (DEMO_DB if args.demo else DEFAULT_DB)
    if not zip_path.exists():
        if args.demo:
            print(
                f"demo slice missing: {zip_path} (see TASKS.md Phase 1: scripts/make_fixtures.py)",
                file=sys.stderr,
            )
        else:
            print(
                f"dump missing: {zip_path}. Run `ctl fetch` or download it from clinicaltrials.gov.",
                file=sys.stderr,
            )
        return 1
    from ct_landscape.db import apply_views, create_enrich_schema
    from ct_landscape.enrich.load import load_shipped_enrichment
    from ct_landscape.funnel import compute_funnel, print_funnel
    from ct_landscape.normalize.build import normalize

    con = connect(db_path)
    if not args.skip_ingest:
        ingest(zip_path, con, limit=args.limit, workers=args.workers)
    normalize(con, workers=args.workers)
    create_enrich_schema(con, drop=True)
    load_shipped_enrichment(con)
    counts = apply_views(con)
    print("views: " + ", ".join(f"{v}={n:,}" for v, n in sorted(counts.items())), file=sys.stderr)
    print_funnel(compute_funnel(con))
    con.close()
    print(f"built {db_path}", file=sys.stderr)
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    from ct_landscape.fetch import DEFAULT_ZIP as FETCH_DEFAULT
    from ct_landscape.fetch import crawl_pager, download_zip

    dest = Path(args.out) if args.out else FETCH_DEFAULT
    if args.pager:
        crawl_pager(dest, max_pages=args.max_pages)
    else:
        download_zip(dest)
    return 0


def cmd_enrich(args: argparse.Namespace) -> int:
    from ct_landscape.db import apply_views, connect, write_meta

    db_path = Path(args.db) if args.db else DEFAULT_DB
    if not db_path.exists():
        print(f"no index at {db_path}; run `ctl build` first", file=sys.stderr)
        return 1
    con = connect(db_path)
    if args.tier == "chembl":
        from ct_landscape.enrich.chembl import run

        census = run(con, refresh=args.refresh)
        write_meta(con, {"chembl_join_census": census})
    else:
        from ct_landscape.enrich.batch import run as run_llm
        from ct_landscape.enrich.load import load_shipped_enrichment

        census = run_llm(con, limit=args.limit, ceiling_usd=args.ceiling, dry_run=args.dry_run)
        write_meta(con, {"llm_batch_census": census})
        if not args.dry_run:
            load_shipped_enrichment(con)
    apply_views(con, fail_on_empty=False)
    con.close()
    return 0


def cmd_sql(args: argparse.Namespace) -> int:
    from ct_landscape.db import connect_sandboxed

    db_path = Path(args.db) if args.db else DEFAULT_DB
    if not db_path.exists():
        print(f"no index at {db_path}; run `ctl build` first", file=sys.stderr)
        return 1
    con = connect_sandboxed(db_path)
    rel = con.sql(args.query)
    if rel is None:
        return 0
    if args.csv:  # write from Python: the sandboxed connection cannot touch the filesystem (by design)
        import csv

        w = csv.writer(sys.stdout)
        w.writerow(rel.columns)
        w.writerows(rel.fetchall())
    else:
        rel.show(max_rows=args.max_rows, max_width=200)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ctl", description="ct-landscape ops CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser(
        "fetch", help="download the CT.gov dump (internal zip endpoint; --pager = v2 API fallback)"
    )
    f.add_argument("--out", help=f"destination zip (default {DEFAULT_ZIP})")
    f.add_argument("--pager", action="store_true", help="crawl the documented /api/v2/studies pager instead")
    f.add_argument("--max-pages", type=int, default=None)
    f.set_defaults(func=cmd_fetch)

    b = sub.add_parser("build", help="ingest → normalize → views into a DuckDB file")
    b.add_argument("--demo", action="store_true", help=f"build from the shipped demo slice into {DEMO_DB}")
    b.add_argument("--zip", help=f"source zip (default {DEFAULT_ZIP}, or {DEMO_ZIP} with --demo)")
    b.add_argument("--db", help=f"output DuckDB path (default {DEFAULT_DB}, or {DEMO_DB} with --demo)")
    b.add_argument("--limit", type=int, default=None, help="ingest only the first N members (pilot runs)")
    b.add_argument("--workers", type=int, default=None, help="parser processes (default: cpu_count-1)")
    b.add_argument(
        "--skip-ingest", action="store_true", help="re-run normalize + views on an already-ingested DB"
    )
    b.set_defaults(func=cmd_build)

    e = sub.add_parser(
        "enrich", help="MoA tiers: chembl (REST fetch + exact-fold join, $0) | llm (batch, Phase 3b)"
    )
    e.add_argument("tier", choices=["chembl", "llm"])
    e.add_argument("--db", default=None)
    e.add_argument("--refresh", action="store_true", help="re-fetch from the network instead of the cache")
    e.add_argument(
        "--limit", type=int, default=None, help="llm: only the top-N assets by trial count (pilot)"
    )
    e.add_argument("--ceiling", type=float, default=35.0, help="llm: hard USD ceiling incl. prior spend")
    e.add_argument(
        "--dry-run", action="store_true", help="llm: print the plan + cost estimate, submit nothing"
    )
    e.set_defaults(func=cmd_enrich)

    s = sub.add_parser("serve", help="FastAPI + chat UI")
    s.add_argument("--db", default=None)
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=_not_implemented("Phase 5"))

    ev = sub.add_parser("eval", help="run the gold-set harness")
    ev.add_argument("--db", default=None)
    ev.set_defaults(func=_not_implemented("Phase 6"))

    q = sub.add_parser("sql", help="read-only SQL against the index (sandboxed connection)")
    q.add_argument("query")
    q.add_argument("--db", default=None)
    q.add_argument("--csv", action="store_true")
    q.add_argument("--max-rows", type=int, default=50)
    q.set_defaults(func=cmd_sql)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
