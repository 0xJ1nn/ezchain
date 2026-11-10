"""Encryption at rest for user-supplied provider credentials.

A provider key never reaches the database in plaintext. The service asks one of two
backends to protect it:

* ``age`` encrypts to an operator-held recipient and keeps only the ciphertext in the
  row. Set ``EZCHAIN_PROVIDER_KEY_AGE_RECIPIENT`` and, for reads,
  ``EZCHAIN_PROVIDER_KEY_AGE_IDENTITY``. The ``age`` binary must be on ``PATH``.
* the OS keyring keeps the secret in the platform credential store and the row keeps an
  opaque reference. Install the ``keyring`` package and a working platform backend.

``EZCHAIN_PROVIDER_KEY_STORE`` selects ``age``, ``keyring`` or ``auto``. ``auto`` uses
age when a recipient is configured and the keyring otherwise. There is no plaintext
fallback: creation refuses with a typed error when the selected store is unavailable,
so a misconfigured deployment cannot silently write a key to its database.
"""

from __future__ import annotations

import base64
import importlib
import secrets
import shutil
import subprocess
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from ..config import Settings

AGE_PREFIX = "age:v1:"
KEYRING_PREFIX = "keyring:v1:"
KEYRING_SERVICE = "ezchain-provider-keys"
AGE_TIMEOUT_SECONDS = 15.0


class CipherUnavailable(RuntimeError):
    """The configured secret store cannot be used. Never contains key material."""

    code = "PROVIDER_KEY_ENCRYPTION_UNAVAILABLE"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SecretCipher(Protocol):
    """Protects one provider key. Implementations never log or return plaintext."""

    name: str

    def ensure_available(self) -> None:
        """Raise :class:`CipherUnavailable` before a key is accepted."""
        ...

    def encrypt(self, secret: str) -> str:
        """Return the stored representation. It must not contain the secret."""
        ...

    def decrypt(self, stored: str) -> str:
        """Return the secret for a provider call. Never serialise the result."""
        ...

    def forget(self, stored: str) -> None:
        """Remove the stored secret. Raise :class:`CipherUnavailable` on failure."""
        ...


Runner = Callable[[Sequence[str], bytes], bytes]
Which = Callable[[str], str | None]


def _run_age(argv: Sequence[str], stdin: bytes) -> bytes:
    try:
        completed = subprocess.run(
            list(argv),
            input=stdin,
            capture_output=True,
            timeout=AGE_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CipherUnavailable("the age binary is not installed") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise CipherUnavailable("the age binary could not be started") from exc
    if completed.returncode != 0:
        # stderr is deliberately dropped: it can name a path, and the caller must never
        # see an error that echoes request material.
        raise CipherUnavailable("age could not complete the encryption operation")
    return completed.stdout


class AgeCipher:
    """Encrypt to an age recipient; decrypt with the operator's identity file."""

    name = "age"

    def __init__(
        self,
        *,
        recipient: str | None,
        identity_file: str | None,
        binary: str = "age",
        runner: Runner = _run_age,
        which: Which = shutil.which,
    ) -> None:
        self._recipient = (recipient or "").strip()
        self._identity_file = (identity_file or "").strip() or None
        self._binary = binary
        self._runner = runner
        self._which = which

    def ensure_available(self) -> None:
        if not self._recipient:
            raise CipherUnavailable(
                "set EZCHAIN_PROVIDER_KEY_AGE_RECIPIENT before storing provider keys"
            )
        if self._which(self._binary) is None:
            raise CipherUnavailable(
                "the age binary is not on PATH; install age or select the keyring store"
            )

    def encrypt(self, secret: str) -> str:
        self.ensure_available()
        armored = self._runner(
            [self._binary, "-a", "-r", self._recipient],
            secret.encode("utf-8"),
        )
        return AGE_PREFIX + base64.b64encode(armored).decode("ascii")

    def decrypt(self, stored: str) -> str:
        if not stored.startswith(AGE_PREFIX):
            raise CipherUnavailable("the stored provider key is not age ciphertext")
        if self._identity_file is None:
            raise CipherUnavailable(
                "set EZCHAIN_PROVIDER_KEY_AGE_IDENTITY before reading provider keys"
            )
        try:
            ciphertext = base64.b64decode(stored[len(AGE_PREFIX) :], validate=True)
        except ValueError as exc:
            raise CipherUnavailable("the stored provider key is malformed") from exc
        output = self._runner(
            [self._binary, "-d", "-i", self._identity_file],
            ciphertext,
        )
        try:
            return output.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CipherUnavailable("age returned an unreadable provider key") from exc

    def forget(self, stored: str) -> None:
        """Ciphertext is removed by the caller; there is nothing external to delete."""


class KeyringCipher:
    """Keep the secret in the OS credential store and store only its reference."""

    name = "keyring"

    def __init__(self, *, service: str = KEYRING_SERVICE, module: Any | None = None) -> None:
        self._service = service
        self._module = module

    def _keyring(self) -> Any:
        if self._module is not None:
            return self._module
        try:
            return importlib.import_module("keyring")
        except ImportError as exc:
            raise CipherUnavailable(
                "the keyring package is not installed; install it or configure age"
            ) from exc

    def ensure_available(self) -> None:
        module = self._keyring()
        try:
            # A read against a missing entry proves the backend answers. The fail
            # backend raises here, which is exactly the refusal we want.
            module.get_password(self._service, "ezchain-availability-probe")
        except Exception as exc:  # noqa: BLE001 - any backend fault means unavailable
            raise CipherUnavailable("the OS keyring backend is not available") from exc

    def encrypt(self, secret: str) -> str:
        self.ensure_available()
        reference = secrets.token_urlsafe(24)
        try:
            self._keyring().set_password(self._service, reference, secret)
        except Exception as exc:  # noqa: BLE001 - never echo the secret
            raise CipherUnavailable("the OS keyring refused the provider key") from exc
        return KEYRING_PREFIX + reference

    def decrypt(self, stored: str) -> str:
        if not stored.startswith(KEYRING_PREFIX):
            raise CipherUnavailable("the stored provider key is not a keyring reference")
        reference = stored[len(KEYRING_PREFIX) :]
        try:
            value = self._keyring().get_password(self._service, reference)
        except Exception as exc:  # noqa: BLE001 - any backend fault means unavailable
            raise CipherUnavailable("the OS keyring could not read the provider key") from exc
        if not value:
            raise CipherUnavailable("the OS keyring no longer holds this provider key")
        return str(value)

    def forget(self, stored: str) -> None:
        if not stored.startswith(KEYRING_PREFIX):
            return
        reference = stored[len(KEYRING_PREFIX) :]
        try:
            module = self._keyring()
            if module.get_password(self._service, reference) is None:
                return
            module.delete_password(self._service, reference)
        except Exception as exc:  # noqa: BLE001 - any backend fault is a failed deletion
            raise CipherUnavailable("the OS keyring could not delete the provider key") from exc


def forget_stored_secret(stored: str, settings: Settings) -> None:
    """Delete a stored key through the backend named by its own prefix.

    The current configuration is not authoritative: a row written by the keyring must be
    deleted through the keyring even if ``EZCHAIN_PROVIDER_KEY_STORE`` now selects age.
    """
    if not stored:
        return
    if stored.startswith(KEYRING_PREFIX):
        KeyringCipher().forget(stored)
        return
    if stored.startswith(AGE_PREFIX):
        AgeCipher(
            recipient=settings.provider_key_age_recipient,
            identity_file=settings.provider_key_age_identity,
            binary=settings.provider_key_age_binary,
        ).forget(stored)
        return
    raise CipherUnavailable("the stored provider key has an unknown storage prefix")


def build_secret_cipher(settings: Settings) -> SecretCipher:
    """Choose the configured store. Availability is checked when a key is created."""
    store = settings.provider_key_store
    if store == "age":
        return AgeCipher(
            recipient=settings.provider_key_age_recipient,
            identity_file=settings.provider_key_age_identity,
            binary=settings.provider_key_age_binary,
        )
    if store == "keyring":
        return KeyringCipher()
    if settings.provider_key_age_recipient:
        return AgeCipher(
            recipient=settings.provider_key_age_recipient,
            identity_file=settings.provider_key_age_identity,
            binary=settings.provider_key_age_binary,
        )
    return KeyringCipher()


__all__ = [
    "AGE_PREFIX",
    "AgeCipher",
    "CipherUnavailable",
    "KEYRING_PREFIX",
    "KeyringCipher",
    "SecretCipher",
    "build_secret_cipher",
    "forget_stored_secret",
]
