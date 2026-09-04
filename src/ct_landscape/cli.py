"""`ctl` — operations CLI (build / enrich / serve / eval / sql).

This is the ops surface only; the product interface is the web chat UI (§7).
Subcommands are registered here and implemented phase by phase (see TASKS.md).
"""

from __future__ import annotations

import argparse
import sys


def _not_implemented(phase: str):
    def run(_args: argparse.Namespace) -> int:
        print(f"not implemented yet — see TASKS.md ({phase})", file=sys.stderr)
        return 2

    return run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ctl", description="ct-landscape ops CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="fetch → ingest → normalize → views into ctg.duckdb")
    b.add_argument("--demo", action="store_true", help="build from the shipped demo slice")
    b.add_argument("--zip", help="path to a ctg-studies zip (default: data/raw/ctg-studies.json.zip)")
    b.add_argument("--db", default="data/ctg.duckdb", help="output DuckDB path")
    b.set_defaults(func=_not_implemented("Phase 1"))

    e = sub.add_parser("enrich", help="ChEMBL join + LLM MoA tier")
    e.add_argument("--db", default="data/ctg.duckdb")
    e.set_defaults(func=_not_implemented("Phase 3"))

    s = sub.add_parser("serve", help="FastAPI + chat UI")
    s.add_argument("--db", default="data/ctg.duckdb")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=_not_implemented("Phase 5"))

    ev = sub.add_parser("eval", help="run the gold-set harness")
    ev.add_argument("--db", default="data/ctg.duckdb")
    ev.set_defaults(func=_not_implemented("Phase 6"))

    q = sub.add_parser("sql", help="read-only SQL against the index")
    q.add_argument("query")
    q.add_argument("--db", default="data/ctg.duckdb")
    q.set_defaults(func=_not_implemented("Phase 1"))
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
