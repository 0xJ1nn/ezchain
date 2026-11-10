"""Provider credential storage: encrypted, scoped, revocable and quiet."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ezchain_api.auth import required_scope, requires_auth
from ezchain_api.config import Settings
from ezchain_api.deps import get_secret_cipher
from ezchain_api.models import ProviderKeyRow
from ezchain_api.routers import provider_keys as provider_keys_router
from ezchain_api.services import provider_keys
from ezchain_api.services.provider_secrets import (
    AgeCipher,
    CipherUnavailable,
    KeyringCipher,
    build_secret_cipher,
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
        {get_secret_cipher: lambda: cipher},
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
            cipher=cipher,
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
