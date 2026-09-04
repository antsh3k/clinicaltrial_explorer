import pytest

from ct_landscape.cli import build_parser, main


def test_help_lists_ops_subcommands(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for cmd in ("build", "enrich", "serve", "eval", "sql"):
        assert cmd in out


def test_unimplemented_subcommand_exits_nonzero():
    assert main(["eval"]) == 2


def test_build_demo_without_slice_fails_cleanly(tmp_path):
    assert main(["build", "--zip", str(tmp_path / "missing.zip"), "--db", str(tmp_path / "x.duckdb")]) == 1
