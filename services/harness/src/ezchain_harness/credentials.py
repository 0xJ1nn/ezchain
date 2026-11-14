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


@dataclass(frozen=True)
class Credential:
    env: str
    _secret: str = field(repr=False)

    @classmethod
    def from_env(cls, name: str, *, env: Mapping[str, str] | None = None) -> Credential:
        source = os.environ if env is None else env
        value = source.get(name, "")
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
