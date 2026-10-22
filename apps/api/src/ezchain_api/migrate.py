"""Programmatic Alembic runner.

Deployments run this once before the API starts::

    python -m ezchain_api.migrate
    ezchain-api migrate
"""

from __future__ import annotations

from alembic import command

from .config import get_settings
from .db import alembic_config


def run_migrations(database_url: str | None = None) -> None:
    """Upgrade the configured database to the current head. Forward-only."""
    settings = get_settings()
    url = database_url or settings.database_url
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")


def main() -> None:
    run_migrations()


if __name__ == "__main__":
    main()
