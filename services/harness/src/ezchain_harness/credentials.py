"""The provider key, read from the environment and rendered as nothing.

A client never takes a key as a constructor argument: a key handed in as an argument is a
key a caller can put in a log line, a trace field or a settings page. The only source is an
environment variable, named at construction and read again on every call so a rotated key is
picked up without rebuilding the client.

:class:`Credential` keeps the secret out of its own text: ``repr`` and ``str`` name the
variable and nothing else. Only :meth:`Credential.secret` returns the value, and the one
place that calls it is the header construction inside a provider client.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from .errors import MissingCredential
from .secrets import secret_override


@dataclass(frozen=True)
class Credential:
    env: str
    _secret: str = field(repr=False)

    @classmethod
    def from_env(cls, name: str, *, env: Mapping[str, str] | None = None) -> Credential:
        # A key resolved from the encrypted store is installed for one call by
        # provide_secret. It wins over the process environment so a chat uses the key
        # that belongs to the workspace, never an unrelated value a host happened to set.
        override = secret_override(name) if env is None else None
        source = os.environ if env is None else env
        value = override if override is not None else source.get(name, "")
        if not value.strip():
            raise MissingCredential(name)
        return cls(env=name, _secret=value)

    def secret(self) -> str:
        return self._secret

    def __str__(self) -> str:
        return f"Credential(env={self.env!r}, secret=[redacted])"

    def __repr__(self) -> str:
        return self.__str__()


__all__ = ["Credential"]
