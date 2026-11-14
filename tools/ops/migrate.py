"""Apply the application's forward-only migrations to a named database."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from ezchain_api.db import alembic_config

from .pg import PgTarget


def migrate(database: str, base: PgTarget | None = None) -> str:
    """Upgrade ``database`` on ``base``'s server to head and return the revision.

    The URL is built here rather than read from settings, so a rehearsal can never be
    pointed at the developer's configured database by an unset environment variable.
    """
    target = (
        base.with_database(database)
        if base is not None
        else PgTarget.parse(f"postgresql://{database}")
    )
    config = alembic_config()
    url = _url(target)
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    return head(config)


def head(config: Config) -> str:
    from alembic.script import ScriptDirectory

    revision = ScriptDirectory.from_config(config).get_current_head()
    if revision is None:  # pragma: no cover - the repository always has revisions
        raise RuntimeError("no Alembic head revision found")
    return revision


def _url(target: PgTarget) -> str:
    from sqlalchemy.engine import URL

    return URL.create(
        "postgresql+psycopg",
        username=target.user,
        password=target.password,
        host=target.host,
        port=target.port,
        database=target.dbname,
    ).render_as_string(hide_password=False)


__all__ = ["head", "migrate"]
