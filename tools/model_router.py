#!/usr/bin/env python3
"""Choose the model for a subagent by UTC clock.

Peak pricing runs Monday through Friday in two windows; everywhere else the
full-rate model is both cheaper and stronger, so it is the default.

    peak (eco)   Mon-Fri 01:00-04:00 UTC and 06:00-10:00 UTC
    off-peak     Mon-Fri 04:00-06:00 and 10:00-01:00 UTC
    weekends     Sat and Sun, all 48 hours

Usage:
    python3 tools/model_router.py                 # the model id to spawn
    python3 tools/model_router.py --json          # id plus the reason
    python3 tools/model_router.py --at 2026-11-14T03:30:00Z   # evaluate a time
    python3 tools/model_router.py --selftest      # the boundary table

Exit status is always 0 unless the arguments are unusable, so a caller can
interpolate the output straight into a spawn command. The decision is a pure
function of UTC: no network, no clock skew beyond the host's, no state.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

NORMAL = "opendesign/deepseek-v4.1-flash"
ECO = "opendesign/deepseek-v4.1-flash-eco"

# Half-open windows [start, end) in UTC hours; Monday=0 .. Friday=4.
PEAK_WINDOWS: tuple[tuple[int, int], ...] = ((1, 4), (6, 10))
PEAK_WEEKDAYS = range(0, 5)


def is_peak(when: dt.datetime) -> bool:
    """True when `when` falls inside a weekday peak window."""
    when = when.astimezone(dt.timezone.utc)
    if when.weekday() not in PEAK_WEEKDAYS:
        return False
    return any(start <= when.hour < end for start, end in PEAK_WINDOWS)


def choose(when: dt.datetime) -> tuple[str, str]:
    """Return (model id, one-line reason) for a moment in time."""
    when = when.astimezone(dt.timezone.utc)
    stamp = when.strftime("%Y-%m-%d %H:%M UTC %a")
    if is_peak(when):
        return ECO, f"{stamp}: weekday peak window, using the eco model"
    if when.weekday() >= 5:
        return NORMAL, f"{stamp}: weekend, off-peak, using the normal model"
    return NORMAL, f"{stamp}: off-peak, using the normal model"


def parse_moment(raw: str) -> dt.datetime:
    text = raw.strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def selftest() -> int:
    """Check the boundaries that decide the price of a swarm."""
    saturday = dt.datetime(2026, 11, 14, 2, 0, tzinfo=dt.timezone.utc)
    monday = dt.datetime(2026, 11, 16, 0, 0, tzinfo=dt.timezone.utc)
    cases = [
        (saturday, NORMAL, "weekend inside a weekday window"),
        (monday.replace(hour=0, minute=59), NORMAL, "one minute before peak"),
        (monday.replace(hour=1), ECO, "peak starts at 01:00"),
        (monday.replace(hour=3, minute=59), ECO, "inside the first window"),
        (monday.replace(hour=4), NORMAL, "peak ends at 04:00"),
        (monday.replace(hour=5, minute=59), NORMAL, "inside the off-peak gap"),
        (monday.replace(hour=6), ECO, "second window starts at 06:00"),
        (monday.replace(hour=9, minute=59), ECO, "inside the second window"),
        (monday.replace(hour=10), NORMAL, "second window ends at 10:00"),
        (monday.replace(hour=23), NORMAL, "late off-peak"),
        (dt.datetime(2026, 11, 21, 7, 0, tzinfo=dt.timezone.utc), NORMAL, "saturday"),
        (dt.datetime(2026, 11, 22, 7, 0, tzinfo=dt.timezone.utc), NORMAL, "sunday"),
    ]
    failures = 0
    for moment, expected, label in cases:
        model, reason = choose(moment)
        ok = model == expected
        failures += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'} {label:<38} {model}")
    print(f"\n{len(cases) - failures}/{len(cases)} boundary cases pass")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pick the subagent model from the UTC clock.")
    parser.add_argument("--at", metavar="ISO8601", help="evaluate this moment instead of now")
    parser.add_argument("--json", action="store_true", help="print the model with its reason")
    parser.add_argument("--selftest", action="store_true", help="check the window boundaries")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    moment = parse_moment(args.at) if args.at else dt.datetime.now(dt.timezone.utc)
    model, reason = choose(moment)
    if args.json:
        print(json.dumps({"model": model, "peak": is_peak(moment), "reason": reason}))
    else:
        print(model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
