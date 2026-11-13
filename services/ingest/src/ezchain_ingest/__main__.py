"""Operator entry point for market retention: plan first, apply deliberately.

    python -m ezchain_ingest                         # dry run: what would be deleted
    python -m ezchain_ingest --apply                 # delete, in one transaction
    python -m ezchain_ingest --raw-days 7            # narrow the raw window once

The dry run is the default because deleting market evidence is not reversible: an operator
reads the count, then decides. The database URL is ``EZCHAIN_DATABASE_URL``; the windows
are the two ``EZCHAIN_RETENTION_*`` variables, validated by :class:`RetentionPolicy`.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from .retention import RetentionError, RetentionPolicy, RetentionPolicyError, prune


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ezchain_ingest")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete the rows the plan reports; without this the run only counts them",
    )
    parser.add_argument("--raw-days", type=int, default=None, help="override the raw window")
    parser.add_argument(
        "--candle-1m-days",
        type=int,
        default=None,
        help="override the 1-minute candle window",
    )
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine

    from ezchain_api.config import get_settings

    try:
        policy = RetentionPolicy.from_env()
        if args.raw_days is not None or args.candle_1m_days is not None:
            policy = RetentionPolicy(
                raw_days=policy.raw_days if args.raw_days is None else args.raw_days,
                candle_1m_days=(
                    policy.candle_1m_days if args.candle_1m_days is None else args.candle_1m_days
                ),
            )
    except RetentionPolicyError as exc:
        print(f"retention: {exc}")
        return 2

    engine = create_engine(get_settings().database_url)
    try:
        with engine.begin() as connection:
            result = prune(connection, policy, apply=args.apply)
    except RetentionError as exc:
        # The reason is the driver's, verbatim: a missing table means the revision has not
        # run, an unreachable server means the URL is wrong. Neither is a silent zero.
        print(f"retention: {exc}")
        return 1
    finally:
        engine.dispose()
    print(json.dumps(result.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
