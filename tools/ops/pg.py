"""Postgres connection plumbing shared by the backup and restore tools.

One place parses a SQLAlchemy URL into libpq connection facts, so the backup, the
restore, and the tests always talk to the database the same way. Passwords never leave
this module except into a child process environment; ``redact()`` is the only form that
may be printed or persisted.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from typing import Any

import psycopg
from sqlalchemy.engine import make_url

# A restore target must look disposable. A typo in a database name must fail closed
# before anything is dropped, exactly as ``tools/check-migrations.sh`` refuses a URL
# that does not name a test database. The rule is the same three markers, deliberately:
# a weaker one ("contains rehearsal") would accept a database actually named for a
# rehearsal and leave a real one droppable.
DISPOSABLE_PATTERN = re.compile(r"test|scratch|_ci")


class OpsError(RuntimeError):
    """A refused or failed operator action, with a message meant for the operator."""


@dataclass(frozen=True)
class PgTarget:
    """One database addressed by the facts libpq needs."""

    host: str | None
    port: int | None
    user: str | None
    password: str | None
    dbname: str

    @classmethod
    def parse(cls, url: str) -> PgTarget:
        parsed = make_url(url)
        if parsed.get_backend_name() != "postgresql":
            raise OpsError(
                f"backup and restore require a Postgres URL, not {parsed.get_backend_name()}: "
                f"{parsed.render_as_string(hide_password=True)}"
            )
        dbname = parsed.database
        if not dbname:
            raise OpsError("the database URL does not name a database")
        return cls(
            host=parsed.host,
            port=parsed.port,
            user=parsed.username,
            password=parsed.password,
            dbname=dbname,
        )

    def with_database(self, dbname: str) -> PgTarget:
        return PgTarget(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            dbname=dbname,
        )

    def redact(self) -> str:
        """A printable identity: host, port and database, never a password."""
        host = self.host or "local socket"
        port = "" if self.port is None else f":{self.port}"
        return f"postgresql://{host}{port}/{self.dbname}"

    def libpq_args(self) -> list[str]:
        args: list[str] = []
        if self.host:
            args += ["-h", self.host]
        if self.port is not None:
            args += ["-p", str(self.port)]
        if self.user:
            args += ["-U", self.user]
        args += ["-d", self.dbname]
        return args

    def child_env(self) -> dict[str, str]:
        """The parent environment plus PGPASSWORD when the URL carries one."""
        env = dict(os.environ)
        if self.password is not None:
            env["PGPASSWORD"] = self.password
        return env

    def connect(self, *, database: str | None = None, autocommit: bool = False) -> Any:
        kwargs: dict[str, Any] = {"dbname": database or self.dbname, "autocommit": autocommit}
        if self.host:
            kwargs["host"] = self.host
        if self.port is not None:
            kwargs["port"] = self.port
        if self.user:
            kwargs["user"] = self.user
        if self.password is not None:
            kwargs["password"] = self.password
        return psycopg.connect(**kwargs)


def require_disposable(target: PgTarget) -> None:
    """Refuse a target that does not advertise itself as a throwaway database."""
    if not DISPOSABLE_PATTERN.search(target.dbname):
        raise OpsError(
            f"refusing to drop or overwrite {target.redact()}: the database name must "
            "contain test, scratch or _ci"
        )


def require_binary(name: str, override: str | None = None) -> str:
    """Locate a Postgres client binary, honouring an explicit override."""
    candidate = override or os.environ.get(name.upper().replace("-", "_") + "_BIN") or name
    path = shutil.which(candidate)
    if path is None:
        raise OpsError(
            f"{name} is not on PATH; install the Postgres client tools or set "
            f"{name.upper().replace('-', '_')}_BIN"
        )
    return path


__all__ = ["DISPOSABLE_PATTERN", "OpsError", "PgTarget", "require_binary", "require_disposable"]
