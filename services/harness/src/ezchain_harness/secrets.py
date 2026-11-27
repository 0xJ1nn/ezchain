"""A scoped in-memory secret for one provider call.

A credential decrypted from the encrypted store must reach the client without becoming a
constructor argument, a settings field, a log line or a trace entry. This module keeps it in
a context variable for the length of one call: :func:`provide_secret` installs the value,
:func:`secret_override` is the only reader, and the context unwinds even when the call raises.

The value is never copied into a dataclass that can be rendered, never serialised and never
written to stdout. A client still reads it through :class:`~.credentials.Credential`, so the
rest of the redaction path is unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

_overrides: ContextVar[Mapping[str, str] | None] = ContextVar(
    "ezchain_provider_secrets", default=None
)


@contextmanager
def provide_secret(name: str, value: str) -> Iterator[None]:
    """Make ``value`` the credential for ``name`` for the duration of this block only."""
    if not name or not value:
        raise ValueError("a scoped secret needs a name and a value")
    token = _overrides.set({**(_overrides.get() or {}), name: value})
    try:
        yield
    finally:
        _overrides.reset(token)


def secret_override(name: str) -> str | None:
    """The scoped value for ``name``, if this call installed one."""
    current = _overrides.get()
    return None if current is None else current.get(name)


__all__ = ["provide_secret", "secret_override"]
