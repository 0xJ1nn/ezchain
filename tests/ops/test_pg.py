"""URL handling, disposal guards and digest comparison — no database needed."""

from __future__ import annotations

import hashlib
import json

import pytest
from tools.ops.cli import main
from tools.ops.fingerprint import compare_tables, fingerprint_digest
from tools.ops.pg import OpsError, PgTarget, require_disposable


def test_a_password_never_appears_in_the_printable_identity() -> None:
    target = PgTarget.parse(
        "postgresql+psycopg://alice:s3cret@db.internal:5433/ezchain_rehearsal_test"
    )

    assert target.redact() == "postgresql://db.internal:5433/ezchain_rehearsal_test"
    assert "s3cret" not in target.redact()


def test_libpq_arguments_never_carry_the_password() -> None:
    target = PgTarget.parse("postgresql+psycopg://alice:s3cret@127.0.0.1:5432/ezchain_scratch")

    args = target.libpq_args()
    assert args == ["-h", "127.0.0.1", "-p", "5432", "-U", "alice", "-d", "ezchain_scratch"]
    assert all("s3cret" not in arg for arg in args)
    assert target.child_env()["PGPASSWORD"] == "s3cret"


def test_a_non_postgres_url_is_refused() -> None:
    with pytest.raises(OpsError, match="require a Postgres URL"):
        PgTarget.parse("sqlite+pysqlite:///tmp/paper.db")


@pytest.mark.parametrize(
    "name",
    ["ezchain", "ezchain_prod", "rehearsal_production"],
)
def test_a_restore_refuses_a_database_that_does_not_look_disposable(name: str) -> None:
    with pytest.raises(OpsError, match="must contain test, scratch or _ci"):
        require_disposable(PgTarget(None, None, None, None, name))


@pytest.mark.parametrize(
    "name",
    [
        "ezchain_test",
        "ezchain_scratch",
        "ezchain_rehearsal_source_20260101T000000Z_test",
        "ezchain_ci",
    ],
)
def test_a_disposable_name_is_accepted(name: str) -> None:
    require_disposable(PgTarget(None, None, None, None, name))


def test_restore_help_explains_the_disposal_requirement() -> None:
    """The refusal text is the operator's only warning, so it must name the rule."""
    with pytest.raises(OpsError) as excinfo:
        require_disposable(PgTarget("127.0.0.1", 5432, "0xj1n", None, "ezchain"))

    assert "refusing to drop or overwrite postgresql://127.0.0.1:5432/ezchain" in str(excinfo.value)


def _table(rows: int, digest: str, missing: bool = False) -> dict[str, object]:
    return {
        "rows": None if missing else rows,
        "digest": None if missing else digest,
        "missing": missing,
    }


def test_compare_reports_a_missing_table_in_either_direction() -> None:
    expected = {"ledger_entries": _table(3, "aaa")}
    actual = {"ledger_entries": _table(0, "", missing=True)}

    assert compare_tables(expected, actual) == ["ledger_entries: missing in restore"]


def test_compare_reports_a_row_count_difference_before_a_digest() -> None:
    expected = {"paper_fills": _table(7, "aaa")}
    actual = {"paper_fills": _table(6, "bbb")}

    assert compare_tables(expected, actual) == ["paper_fills: 7 row(s) in source, 6 in restore"]


def test_compare_names_a_same_count_row_difference() -> None:
    """A short restore that kept the count but changed a row is still a failed drill."""
    expected = {"ledger_entries": _table(33, "aaa")}
    actual = {"ledger_entries": _table(33, "bbb")}

    assert compare_tables(expected, actual) == [
        "ledger_entries: same row count (33) but different rows"
    ]


def test_compare_is_silent_when_the_digests_agree() -> None:
    expected = {"ledger_entries": _table(33, "aaa"), "paper_fills": _table(7, "ccc")}

    assert (
        compare_tables(
            expected, {"ledger_entries": _table(33, "aaa"), "paper_fills": _table(7, "ccc")}
        )
        == []
    )


def test_compare_ignores_tables_outside_the_requested_set() -> None:
    expected = {"ledger_entries": _table(1, "aaa")}
    actual = {"ledger_entries": _table(1, "aaa"), "api_keys": _table(4, "zzz")}

    assert compare_tables(expected, actual, only=("ledger_entries",)) == []


def test_the_fingerprint_is_stable_across_key_order() -> None:
    first = {"ledger_entries": _table(2, "aaa"), "paper_fills": _table(1, "bbb")}
    second = {"paper_fills": _table(1, "bbb"), "ledger_entries": _table(2, "aaa")}

    assert fingerprint_digest(first) == fingerprint_digest(second)
    assert len(fingerprint_digest(first)) == 64


def test_the_cli_refuses_a_live_looking_restore_target_with_exit_2(tmp_path) -> None:
    """A refusal is exit 2, distinct from a verification failure at exit 1."""
    dump = tmp_path / "ezchain.dump"
    dump.write_bytes(b"not a dump")
    digest = hashlib.sha256(b"not a dump").hexdigest()
    manifest = tmp_path / "ezchain.manifest.json"
    manifest.write_text(
        json.dumps({"dump": {"path": dump.name, "sha256": digest}, "tables": {}, "source": {}}),
        encoding="utf-8",
    )

    code = main(
        [
            "restore",
            "--database",
            "postgresql+psycopg://0xj1n@127.0.0.1:5432/ezchain",
            "--manifest",
            str(manifest),
        ]
    )

    assert code == 2
