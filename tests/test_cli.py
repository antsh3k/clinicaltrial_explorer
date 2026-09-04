import pytest

from ct_landscape.cli import build_parser, main


def test_help_lists_ops_subcommands(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for cmd in ("build", "enrich", "serve", "eval", "sql"):
        assert cmd in out


def test_eval_without_index_fails_cleanly(tmp_path):
    assert main(["eval", "--db", str(tmp_path / "missing.duckdb")]) == 1


def test_build_demo_without_slice_fails_cleanly(tmp_path):
    assert main(["build", "--zip", str(tmp_path / "missing.zip"), "--db", str(tmp_path / "x.duckdb")]) == 1


def test_serve_without_full_index_points_at_existing_demo(tmp_path, monkeypatch, capsys):
    from ct_landscape import cli

    demo = tmp_path / "ctg_demo.duckdb"
    demo.write_bytes(b"")
    monkeypatch.setattr(cli, "DEFAULT_DB", tmp_path / "ctg.duckdb")
    monkeypatch.setattr(cli, "DEMO_DB", demo)
    assert main(["serve"]) == 1
    err = capsys.readouterr().err
    assert "no index at" in err
    assert "--demo" in err and str(demo) in err


def test_serve_without_any_index_does_not_mention_demo(tmp_path, monkeypatch, capsys):
    from ct_landscape import cli

    monkeypatch.setattr(cli, "DEFAULT_DB", tmp_path / "ctg.duckdb")
    monkeypatch.setattr(cli, "DEMO_DB", tmp_path / "ctg_demo.duckdb")
    assert main(["serve"]) == 1
    err = capsys.readouterr().err
    assert "no index at" in err and "run `ctl build` first" in err
    assert "--demo" not in err
