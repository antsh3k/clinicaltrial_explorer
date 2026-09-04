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
    con = connect(db_path)
    ingest(zip_path, con, limit=args.limit, workers=args.workers)
    # normalize + views are added in Phase 2
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
    if args.csv:
        rel.write_csv("/dev/stdout")
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
    b.set_defaults(func=cmd_build)

    e = sub.add_parser("enrich", help="ChEMBL join + LLM MoA tier")
    e.add_argument("--db", default=None)
    e.set_defaults(func=_not_implemented("Phase 3"))

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
