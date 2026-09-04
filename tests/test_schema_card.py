"""Every worked SQL statement in the schema card must pass the sandbox and run on the index. The card is the agent's
only SQL tutorial: a broken example teaches a broken pattern (a per-company loop exhausted the turn cap once)."""

import pytest

from ct_landscape.agent import tools as T
from ct_landscape.agent.schema_card import CARD_TEMPLATE, schema_card
from tests.test_api import db_path  # noqa: F401  (module-scoped mini index)


def worked_sql() -> list[tuple[str, str]]:
    """(comment, statement) pairs from the '## Worked SQL' section; a statement ends at ';'."""
    body = CARD_TEMPLATE.split("## Worked SQL", 1)[1]
    out, comment, buf = [], [], []
    for line in body.splitlines():
        if line.startswith("--"):
            if not buf:
                comment.append(line[2:].strip())
            continue
        if not line.strip():
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            out.append((" ".join(comment), "\n".join(buf)))
            comment, buf = [], []
    assert not buf, "unterminated worked SQL statement"
    return out


def test_card_has_the_per_entity_rule_and_example():
    assert "PER-ENTITY RULE" in CARD_TEMPLATE and "QUALIFY row_number()" in CARD_TEMPLATE
    assert any("ONE statement" in c for c, _ in worked_sql())


@pytest.mark.parametrize("comment,sql", worked_sql(), ids=lambda x: x[:50] if isinstance(x, str) else None)
def test_worked_sql_runs_in_the_sandbox(db_path, comment, sql):  # noqa: F811
    con = T.open_sandboxed(db_path)
    try:
        res = T.sandboxed_query(con, sql)  # raises SqlRejected on a sandbox or binder failure
        assert res.columns, comment
    finally:
        con.close()


def test_rendered_card_matches_template(db_path):  # noqa: F811
    con = T.open_sandboxed(db_path)
    try:
        card = schema_card(con)
    finally:
        con.close()
    assert "PER-ENTITY RULE" in card and "{snapshot_date}" not in card
