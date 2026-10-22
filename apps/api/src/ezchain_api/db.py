"""Database engine, session factory and Alembic head inspection."""

from __future__ import annotations

import os
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True, future=True)


@lru_cache
def session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=engine(), expire_on_commit=False, future=True)


def get_session() -> Iterator[Session]:
    with session_factory()() as session:
        yield session


def create_schema() -> None:
    """Dev/test shortcut. Production schema changes only through Alembic."""
    if get_settings().environment == "prod":
        raise RuntimeError(
            "create_schema() is disabled in prod; run `ezchain-api migrate` "
            "(or `python -m ezchain_api.migrate`) instead"
        )
    from . import models  # noqa: F401  (register models)

    Base.metadata.create_all(bind=engine())


def alembic_ini_path() -> Path:
    """Locate ``apps/api/alembic.ini`` from the repo root or an installed layout."""
    override = os.environ.get("EZCHAIN_ALEMBIC_INI")
    candidates = [
        Path(override) if override else None,
        Path.cwd() / "alembic.ini",
        Path.cwd() / "apps/api/alembic.ini",
        Path(__file__).resolve().parents[2] / "alembic.ini",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    searched = ", ".join(str(item) for item in candidates if item is not None)
    raise RuntimeError(f"alembic.ini not found; set EZCHAIN_ALEMBIC_INI (searched: {searched})")


def alembic_config() -> Config:
    path = alembic_ini_path()
    config = Config(str(path))
    config.set_main_option("script_location", str(path.parent / "alembic"))
    return config


def head_revision() -> str | None:
    """The latest revision in the migration scripts, or None if the directory is empty."""
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def database_revision(session: Session) -> str | None:
    """The applied revision, or None for an unstamped database."""
    try:
        value = session.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        return None
    return None if value is None else str(value)
