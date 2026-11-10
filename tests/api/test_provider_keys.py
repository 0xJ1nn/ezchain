"""Provider credential storage: encrypted, scoped, revocable and quiet."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ezchain_adapters import JupiterClient
from ezchain_adapters.jupiter import KEYED_BASE, SOL_MINT
from ezchain_api.auth import required_scope, requires_auth
from ezchain_api.config import Settings, get_settings
from ezchain_api.deps import get_secret_cipher, get_secret_forgetter
from ezchain_api.models import ProviderKeyRow
from ezchain_api.routers import provider_keys as provider_keys_router
from ezchain_api.services import provider_keys, provider_secrets
from ezchain_api.services.provider_secrets import (
    AGE_PREFIX,
    KEYRING_PREFIX,
    AgeCipher,
    CipherUnavailable,
    KeyringCipher,
    build_secret_cipher,
    forget_stored_secret,
)

TEST_KEY = "TEST_KEY_DO_NOT_USE"


class MemoryCipher:
    """A deterministic test double; the real stores live in ``provider_secrets``."""

    name = "memory"

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def ensure_available(self) -> None:
        return None

    def encrypt(self, secret: str) -> str:
        stored = f"memory:v1:{len(self.values)}"
        self.values[stored] = secret
        return stored

    def decrypt(self, stored: str) -> str:
        return self.values[stored]

    def forget(self, stored: str) -> None:
        self.values.pop(stored, None)


class UnavailableCipher(MemoryCipher):
    def ensure_available(self) -> None:
        raise CipherUnavailable("the test store is unavailable")


def make_client(client_for: Any, cipher: MemoryCipher) -> TestClient:
    return client_for(
        provider_keys_router.router,
        {
            get_secret_cipher: lambda: cipher,
            get_secret_forgetter: lambda: cipher.forget,
        },
    )


def test_create_returns_a_fingerprint_and_not_the_key(
    client_for: Any, session: Any, settings: Settings
) -> None:
    cipher = MemoryCipher()
    client = make_client(client_for, cipher)

    response = client.post(
        "/api/v1/provider-keys",
        json={"provider": "jupiter", "label": "desk", "key": TEST_KEY},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["fingerprint"] == provider_keys.fingerprint(TEST_KEY)
    assert body["provider"] == "jupiter"
    assert body["workspace_id"] == settings.workspace_id
    assert TEST_KEY not in response.text
    assert "ciphertext" not in body

    row = session.get(ProviderKeyRow, body["id"])
    assert row is not None
    assert TEST_KEY not in row.secret_ciphertext
    assert row.secret_ciphertext not in response.text
    assert row.secret_ciphertext in cipher.values


def test_list_returns_metadata_and_never_the_key(
    client_for: Any, session: Any, settings: Settings
) -> None:
    cipher = MemoryCipher()
    client = make_client(client_for, cipher)
    created = client.post(
        "/api/v1/provider-keys",
        json={"provider": "jupiter", "label": "desk", "key": TEST_KEY},
    ).json()

    response = client.get("/api/v1/provider-keys")

    assert response.status_code == 200
    assert TEST_KEY not in response.text
    assert "memory:v1:" not in response.text
    items = response.json()
    assert len(items) == 1
    assert items[0]["id"] == created["id"]
    assert items[0]["fingerprint"] == created["fingerprint"]
    assert "secret_ciphertext" not in items[0]
    assert items[0]["revoked_at"] is None


def test_revoke_clears_material_and_stops_use(
    client_for: Any, session: Any, settings: Settings
) -> None:
    cipher = MemoryCipher()
    client = make_client(client_for, cipher)
    created = client.post(
        "/api/v1/provider-keys",
        json={"provider": "jupiter", "key": TEST_KEY},
    ).json()

    assert (
        provider_keys.resolve_secret(
            session,
            workspace_id=settings.workspace_id,
            provider="jupiter",
            cipher=cipher,
        )
        == TEST_KEY
    )

    revoked = client.delete(f"/api/v1/provider-keys/{created['id']}")
    assert revoked.status_code == 204

    with pytest.raises(provider_keys.ProviderKeyNotFound):
        provider_keys.resolve_secret(
            session,
            workspace_id=settings.workspace_id,
            provider="jupiter",
            cipher=cipher,
        )
    row = session.get(ProviderKeyRow, created["id"])
    assert row is not None
    assert row.secret_ciphertext == ""
    assert cipher.values == {}

    again = client.delete(f"/api/v1/provider-keys/{created['id']}")
    assert again.status_code == 404
    assert again.json()["detail"]["code"] == "PROVIDER_KEY_NOT_FOUND"


def test_workspace_boundary_hides_and_protects_keys(session: Any) -> None:
    cipher = MemoryCipher()
    row = provider_keys.create_key(
        session,
        workspace_id="alpha",
        provider="jupiter",
        secret=TEST_KEY,
        cipher=cipher,
    )

    assert provider_keys.list_keys(session, workspace_id="beta") == []
    with pytest.raises(provider_keys.ProviderKeyNotFound):
        provider_keys.resolve_secret(
            session,
            workspace_id="beta",
            provider="jupiter",
            cipher=cipher,
        )
    assert (
        provider_keys.revoke_key(
            session,
            workspace_id="beta",
            key_id=row.id,
            forget=cipher.forget,
        )
        is False
    )
    assert (
        provider_keys.resolve_secret(
            session,
            workspace_id="alpha",
            provider="jupiter",
            cipher=cipher,
        )
        == TEST_KEY
    )


def test_create_refuses_when_the_store_is_unavailable(
    client_for: Any,
) -> None:
    client = make_client(client_for, UnavailableCipher())

    response = client.post(
        "/api/v1/provider-keys",
        json={"provider": "jupiter", "key": TEST_KEY},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "PROVIDER_KEY_ENCRYPTION_UNAVAILABLE"
    assert TEST_KEY not in response.text


def test_errors_do_not_echo_the_key(client_for: Any) -> None:
    client = make_client(client_for, MemoryCipher())

    unknown = client.post(
        "/api/v1/provider-keys",
        json={"provider": "not-a-provider", "key": TEST_KEY},
    )

    assert unknown.status_code == 422
    assert TEST_KEY not in unknown.text


def test_the_key_never_reaches_a_log_line(
    client_for: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    cipher = MemoryCipher()
    client = make_client(client_for, cipher)

    created = client.post(
        "/api/v1/provider-keys",
        json={"provider": "jupiter", "key": TEST_KEY},
    ).json()
    client.get("/api/v1/provider-keys")
    client.delete(f"/api/v1/provider-keys/{created['id']}")

    assert TEST_KEY not in caplog.text


def test_provider_key_routes_are_admin_only(settings: Settings) -> None:
    assert requires_auth("GET", "/api/v1/provider-keys", settings) is True
    assert required_scope("GET", "/api/v1/provider-keys") == "admin"
    assert required_scope("POST", "/api/v1/provider-keys") == "admin"
    assert required_scope("DELETE", "/api/v1/provider-keys/some-id") == "admin"


def test_auto_selects_age_when_a_recipient_is_configured(settings: Settings) -> None:
    configured = settings.model_copy(update={"provider_key_age_recipient": "age1testrecipient"})

    assert build_secret_cipher(configured).name == "age"


def test_auto_selects_the_keyring_without_a_recipient(settings: Settings) -> None:
    assert build_secret_cipher(settings).name == "keyring"


def test_age_refuses_when_the_binary_is_missing() -> None:
    cipher = AgeCipher(
        recipient="age1testrecipient",
        identity_file="/tmp/identity",
        binary="age",
        which=lambda _name: None,
    )

    with pytest.raises(CipherUnavailable) as caught:
        cipher.ensure_available()

    assert "age binary" in caught.value.message


def test_age_encrypts_and_decrypts_through_the_runner() -> None:
    calls: list[tuple[list[str], bytes]] = []

    def runner(argv: Any, stdin: bytes) -> bytes:
        calls.append((list(argv), stdin))
        if "-d" in argv:
            return TEST_KEY.encode()
        return b"armored-ciphertext"

    cipher = AgeCipher(
        recipient="age1testrecipient",
        identity_file="/tmp/identity",
        runner=runner,
        which=lambda _name: "/usr/bin/age",
    )

    stored = cipher.encrypt(TEST_KEY)

    assert stored.startswith("age:v1:")
    assert TEST_KEY not in stored
    assert TEST_KEY not in " ".join(calls[0][0])
    assert calls[0][1] == TEST_KEY.encode()
    assert cipher.decrypt(stored) == TEST_KEY


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, reference: str) -> str | None:
        return self.values.get((service, reference))

    def set_password(self, service: str, reference: str, value: str) -> None:
        self.values[(service, reference)] = value

    def delete_password(self, service: str, reference: str) -> None:
        self.values.pop((service, reference), None)


def test_keyring_keeps_only_a_reference() -> None:
    module = FakeKeyring()
    cipher = KeyringCipher(module=module)

    stored = cipher.encrypt(TEST_KEY)

    assert stored.startswith("keyring:v1:")
    assert TEST_KEY not in stored
    assert cipher.decrypt(stored) == TEST_KEY
    cipher.forget(stored)
    assert module.values == {}


def test_malformed_body_never_echoes_the_key(client_for: Any) -> None:
    client = make_client(client_for, MemoryCipher())

    missing_provider = client.post("/api/v1/provider-keys", json={"key": TEST_KEY})
    array_body = client.post("/api/v1/provider-keys", json=[TEST_KEY])
    string_body = client.post(
        "/api/v1/provider-keys",
        content=f'"{TEST_KEY}"',
        headers={"content-type": "application/json"},
    )

    for response in (missing_provider, array_body, string_body):
        assert response.status_code == 422
        assert TEST_KEY not in response.text
        assert "input" not in response.json()["detail"][0]


def test_production_requires_a_fingerprint_pepper(client_for: Any, settings: Settings) -> None:
    cipher = MemoryCipher()
    prod = settings.model_copy(
        update={
            "environment": "prod",
            "provider_key_fingerprint_pepper": None,
            "api_key_pepper": None,
        }
    )
    client = client_for(
        provider_keys_router.router,
        {
            get_settings: lambda: prod,
            get_secret_cipher: lambda: cipher,
            get_secret_forgetter: lambda: cipher.forget,
        },
    )

    response = client.post(
        "/api/v1/provider-keys",
        json={"provider": "jupiter", "key": TEST_KEY},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "PROVIDER_KEY_PEPPER_REQUIRED"
    assert TEST_KEY not in response.text


def test_production_uses_the_configured_fingerprint_pepper(
    client_for: Any, settings: Settings
) -> None:
    cipher = MemoryCipher()
    prod = settings.model_copy(
        update={
            "environment": "prod",
            "provider_key_fingerprint_pepper": SecretStr("prod-pepper"),
            "api_key_pepper": None,
        }
    )
    client = client_for(
        provider_keys_router.router,
        {
            get_settings: lambda: prod,
            get_secret_cipher: lambda: cipher,
            get_secret_forgetter: lambda: cipher.forget,
        },
    )

    response = client.post(
        "/api/v1/provider-keys",
        json={"provider": "jupiter", "key": TEST_KEY},
    )

    assert response.status_code == 201
    assert response.json()["fingerprint"] == provider_keys.fingerprint(TEST_KEY, "prod-pepper")


def test_production_falls_back_to_the_api_key_pepper(client_for: Any, settings: Settings) -> None:
    cipher = MemoryCipher()
    prod = settings.model_copy(
        update={
            "environment": "prod",
            "provider_key_fingerprint_pepper": None,
            "api_key_pepper": SecretStr("shared-pepper"),
        }
    )
    client = client_for(
        provider_keys_router.router,
        {
            get_settings: lambda: prod,
            get_secret_cipher: lambda: cipher,
            get_secret_forgetter: lambda: cipher.forget,
        },
    )

    response = client.post(
        "/api/v1/provider-keys",
        json={"provider": "jupiter", "key": TEST_KEY},
    )

    assert response.status_code == 201
    assert response.json()["fingerprint"] == provider_keys.fingerprint(TEST_KEY, "shared-pepper")


def test_fingerprint_is_keyed_by_the_pepper() -> None:
    first = provider_keys.fingerprint(TEST_KEY, "pepper-a")
    second = provider_keys.fingerprint(TEST_KEY, "pepper-b")

    assert first != second
    assert first == provider_keys.fingerprint(TEST_KEY, "pepper-a")
    assert first != provider_keys.fingerprint(TEST_KEY)


def test_invalid_label_is_rejected_before_encryption(session: Any, settings: Settings) -> None:
    cipher = MemoryCipher()

    with pytest.raises(provider_keys.ProviderKeyInvalid):
        provider_keys.create_key(
            session,
            workspace_id=settings.workspace_id,
            provider="jupiter",
            secret=TEST_KEY,
            label="x" * 121,
            cipher=cipher,
        )

    assert cipher.values == {}


class FailingSession:
    def __init__(self) -> None:
        self.rolled_back = False
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)

    def commit(self) -> None:
        raise RuntimeError("database is down")

    def rollback(self) -> None:
        self.rolled_back = True


def test_commit_failure_forgets_the_encrypted_secret(settings: Settings) -> None:
    cipher = MemoryCipher()
    session = FailingSession()

    with pytest.raises(RuntimeError):
        provider_keys.create_key(
            session,
            workspace_id=settings.workspace_id,
            provider="jupiter",
            secret=TEST_KEY,
            cipher=cipher,
        )

    assert session.rolled_back is True
    assert cipher.values == {}


def test_a_failed_delete_keeps_the_reference_so_a_retry_can_purge_it(
    session: Any, settings: Settings
) -> None:
    cipher = MemoryCipher()
    row = provider_keys.create_key(
        session,
        workspace_id=settings.workspace_id,
        provider="jupiter",
        secret=TEST_KEY,
        cipher=cipher,
    )
    stored = row.secret_ciphertext
    attempts: list[str] = []
    refusing = {"yes": True}

    def forget(value: str) -> None:
        attempts.append(value)
        if refusing["yes"]:
            raise CipherUnavailable("the external store refused the deletion")
        cipher.forget(value)

    with pytest.raises(provider_keys.ProviderKeyForgetFailed) as caught:
        provider_keys.revoke_key(
            session,
            workspace_id=settings.workspace_id,
            key_id=row.id,
            forget=forget,
        )

    assert caught.value.retryable is True
    session.expire_all()
    pending = session.get(ProviderKeyRow, row.id)
    assert pending is not None
    # Revoked already, so no provider call can read it...
    assert pending.revoked_at is not None
    with pytest.raises(provider_keys.ProviderKeyNotFound):
        provider_keys.resolve_secret(
            session,
            workspace_id=settings.workspace_id,
            provider="jupiter",
            cipher=cipher,
        )
    # ...but the row still holds the only pointer to the external copy.
    assert pending.secret_ciphertext == stored
    assert stored in cipher.values

    refusing["yes"] = False
    assert (
        provider_keys.revoke_key(
            session,
            workspace_id=settings.workspace_id,
            key_id=row.id,
            forget=forget,
        )
        is True
    )

    session.expire_all()
    purged = session.get(ProviderKeyRow, row.id)
    assert purged is not None
    # The retry deleted the original reference, not a copy of it, and only then cleared it.
    assert attempts == [stored, stored]
    assert purged.secret_ciphertext == ""
    assert cipher.values == {}


def test_revoke_is_retryable_through_the_route(client_for: Any, session: Any) -> None:
    cipher = MemoryCipher()
    attempts: list[str] = []
    refusing = {"yes": True}

    def forgetter(stored: str) -> None:
        attempts.append(stored)
        if refusing["yes"]:
            raise CipherUnavailable("the external store refused the deletion")
        cipher.forget(stored)

    client = client_for(
        provider_keys_router.router,
        {
            get_secret_cipher: lambda: cipher,
            get_secret_forgetter: lambda: forgetter,
        },
    )
    created = client.post(
        "/api/v1/provider-keys",
        json={"provider": "jupiter", "key": TEST_KEY},
    ).json()
    row = session.get(ProviderKeyRow, created["id"])
    assert row is not None
    stored = row.secret_ciphertext

    first = client.delete(f"/api/v1/provider-keys/{created['id']}")
    assert first.status_code == 503
    assert first.json()["detail"]["code"] == "PROVIDER_KEY_FORGET_FAILED"
    assert first.json()["detail"]["retryable"] is True

    refusing["yes"] = False
    second = client.delete(f"/api/v1/provider-keys/{created['id']}")
    # A second attempt completes the purge instead of 404-ing on a key that is only
    # half-revoked.
    assert second.status_code == 204
    assert attempts == [stored, stored]
    assert cipher.values == {}


def test_revoke_returns_503_when_the_external_delete_fails(client_for: Any) -> None:
    cipher = MemoryCipher()

    def failing_forget(_stored: str) -> None:
        raise CipherUnavailable("the external store refused the deletion")

    client = client_for(
        provider_keys_router.router,
        {
            get_secret_cipher: lambda: cipher,
            get_secret_forgetter: lambda: failing_forget,
        },
    )
    created = client.post(
        "/api/v1/provider-keys",
        json={"provider": "jupiter", "key": TEST_KEY},
    ).json()

    response = client.delete(f"/api/v1/provider-keys/{created['id']}")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "PROVIDER_KEY_FORGET_FAILED"
    assert TEST_KEY not in response.text


def test_forget_dispatches_by_the_stored_prefix(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    calls: list[str] = []

    class Spy:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def forget(self, stored: str) -> None:
            calls.append(stored)

    monkeypatch.setattr(provider_secrets, "KeyringCipher", Spy)
    forget_stored_secret(KEYRING_PREFIX + "reference", settings)
    assert calls == [KEYRING_PREFIX + "reference"]

    monkeypatch.setattr(provider_secrets, "AgeCipher", Spy)
    forget_stored_secret(AGE_PREFIX + "ciphertext", settings)
    assert calls == [KEYRING_PREFIX + "reference", AGE_PREFIX + "ciphertext"]


class DeniedKeyring(FakeKeyring):
    def delete_password(self, service: str, reference: str) -> None:
        raise RuntimeError("deletion denied")


def test_keyring_forget_surfaces_a_deletion_failure() -> None:
    module = DeniedKeyring()
    module.values[("ezchain-provider-keys", "reference")] = "stored"
    cipher = KeyringCipher(module=module)

    with pytest.raises(CipherUnavailable):
        cipher.forget(KEYRING_PREFIX + "reference")


def test_keyring_forget_treats_a_missing_entry_as_done() -> None:
    cipher = KeyringCipher(module=FakeKeyring())

    cipher.forget(KEYRING_PREFIX + "missing")


@respx.mock
async def test_the_resolved_key_is_sent_to_the_keyed_endpoint(
    session: Any, settings: Settings
) -> None:
    cipher = MemoryCipher()
    provider_keys.create_key(
        session,
        workspace_id=settings.workspace_id,
        provider="jupiter",
        secret=TEST_KEY,
        cipher=cipher,
    )

    async def api_key() -> str:
        return provider_keys.resolve_secret(
            session,
            workspace_id=settings.workspace_id,
            provider="jupiter",
            cipher=cipher,
        )

    fixture = Path(__file__).resolve().parents[2] / "tests/adapters/fixtures/jupiter_quote_buy.json"
    route = respx.get(f"{KEYED_BASE}/swap/v1/quote").mock(
        return_value=httpx.Response(200, json=json.loads(fixture.read_text()))
    )
    client = JupiterClient(api_key_provider=api_key)

    async with client as scoped:
        await scoped.quote_paper(token_address=SOL_MINT, side="buy", amount_usd=Decimal("1"))

    assert route.calls[0].request.headers["x-api-key"] == TEST_KEY
    assert str(route.calls[0].request.url).startswith(KEYED_BASE)
