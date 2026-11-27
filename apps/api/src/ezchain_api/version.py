"""The one product version.

The repository-root ``VERSION`` file is the only source of the ezCHAIN product version.
Every surface that shows it — the API probes, the OpenAPI document and the web footer —
reads this module (or the web reader that mirrors it) rather than carrying its own copy.

A missing, unreadable or malformed file is a startup error. There is deliberately no
fallback: a deployment that cannot say which product it is running must not guess.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_FILENAME = "VERSION"
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_VERSION_FILE_ENV = "EZCHAIN_VERSION_FILE"


def _candidate_files() -> list[Path]:
    """Where to look, in order. An explicit path is authoritative, never a hint."""
    override = os.environ.get(_VERSION_FILE_ENV)
    if override:
        return [Path(override)]

    candidates: list[Path] = []
    # Source checkout: walk up from this module to the repository root. Installed image:
    # walk up from the working directory, where the image places the copied file.
    for start in (Path(__file__).resolve().parent, Path.cwd()):
        for parent in (start, *start.parents):
            candidate = parent / _FILENAME
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def load_product_version() -> str:
    """Read and validate the product version, or raise rather than guess."""
    candidates = _candidate_files()
    tried: list[str] = []
    for candidate in candidates:
        tried.append(str(candidate))
        if not candidate.is_file():
            continue
        try:
            raw = candidate.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise RuntimeError(
                f"ezCHAIN product version is unreadable at {candidate}: {exc}"
            ) from exc
        if not _SEMVER.fullmatch(raw):
            raise RuntimeError(
                f"ezCHAIN product version at {candidate} is not a semantic version: {raw!r}"
            )
        return raw
    raise RuntimeError(
        "ezCHAIN product version is unavailable: no readable VERSION file. "
        f"Tried: {', '.join(tried)}. Set {_VERSION_FILE_ENV} to the file's path."
    )


PRODUCT_VERSION = load_product_version()
