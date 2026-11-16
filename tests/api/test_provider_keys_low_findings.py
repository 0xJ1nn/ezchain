"""The four low provider-key findings: honest probes, atomic replace, healed index."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from test_provider_keys import TEST_KEY, FakeKeyring, MemoryCipher

from ezchain_api.db import alembic_config
from ezchain_api.models import ProviderKeyRow
from ezchain_api.services import provider_keys
from ezchain_api.services.provider_secrets import CipherUnavailable, KeyringCipher

WS = "ezchain-test"


class DeleteRaisesKeyring(FakeKeyring):
    def delete_password(self, service: str, reference: str) -> None:
        raise RuntimeError("backend refused the delete")


class DeleteNoopKeyring(FakeKeyring):
    def delete_password(self, service: str, reference: str) -> None:
        return None


def test_probe_delete_failure_is_retryable_and_names_the_entry() -> None:
    cipher = KeyringCipher(module=DeleteRaisesKeyring())
    with pytest.raises(CipherUnavailable) as caught:
        cipher.ensure_available()
    assert caught.value.retryable is True
    assert caught.value.detail["probe_entry"].startswith("ezchain-availability-")


def test_probe_delete_that_does_nothing_fails_availability() -> None:
    cipher = KeyringCipher(module=DeleteNoopKeyring())
    with pytest.raises(CipherUnavailable) as caught:
        cipher.ensure_available()
    assert "did not remove" in caught.value.message
    assert caught.value.retryable is True


def test_a_commit_failure_during_replace_leaves_the_previous_key_active(
    session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cipher = MemoryCipher()
    provider_keys.create_key(
        session,
        workspace_id=WS,
        provider="jupiter",
        secret=TEST_KEY,
        cipher=cipher,
    )
    original = [row for row in provider_keys.list_keys(session, WS) if row.revoked_at is None][0]
    real_commit = session.commit
    state = {"failed": False}

    def flaky_commit() -> None:
        if not state["failed"]:
            state["failed"] = True
            raise RuntimeError("commit failed after the retirement was staged")
        real_commit()

    monkeypatch.setattr(session, "commit", flaky_commit)
    with pytest.raises(RuntimeError):
        provider_keys.create_key(
            session,
            workspace_id=WS,
            provider="jupiter",
            secret="OTHER_TEST_KEY_DO_NOT_USE",
            cipher=cipher,
            replace=True,
            forget=cipher.forget,
        )

    session.expire_all()
    still_active = session.get(ProviderKeyRow, original.id)
    assert still_active is not None
    assert still_active.revoked_at is None
    assert (
        provider_keys.resolve_secret(session, workspace_id=WS, provider="jupiter", cipher=cipher)
        == TEST_KEY
    )


def test_a_database_stamped_at_0016_without_the_index_gains_it(tmp_path: Path) -> None:
    database = tmp_path / "stamped-0016.db"
    url = f"sqlite+pysqlite:///{database}"
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["database_url"] = url
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE provider_keys ("
                "id VARCHAR(36) PRIMARY KEY, workspace_id VARCHAR(120) NOT NULL, "
                "provider VARCHAR(80) NOT NULL, label VARCHAR(120), fingerprint VARCHAR(64) "
                "NOT NULL, secret_ciphertext TEXT NOT NULL, created_at DATETIME NOT NULL, "
                "last_used_at DATETIME, revoked_at DATETIME)"
            )
        )
    command.stamp(config, "0016_provider_keys")
    command.upgrade(config, "head")

    indexes = {index["name"] for index in inspect(engine).get_indexes("provider_keys")}
    assert "uq_provider_keys_active" in indexes


@pytest.mark.skipif(
    not os.environ.get("EZCHAIN_TEST_DATABASE_URL"),
    reason="the Postgres-specific index check needs EZCHAIN_TEST_DATABASE_URL",
)
def test_the_active_key_index_is_unique_on_postgres() -> None:
    url = os.environ["EZCHAIN_TEST_DATABASE_URL"]
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    engine = create_engine(url)
    index = next(
        item
        for item in inspect(engine).get_indexes("provider_keys")
        if item["name"] == "uq_provider_keys_active"
    )
    assert bool(index["unique"]) is True
