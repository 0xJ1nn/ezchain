#!/usr/bin/env python3
"""Prove the dependency pins before anything installs from them.

The locks in the repository root are the install source. This script gives that
claim two paths:

``--fast`` (default)
    Static, no network, no virtual environment. Every lock entry must be an
    exact ``==`` version carrying at least one ``sha256`` hash; every direct
    requirement in ``requirements.txt``, ``requirements-runtime.txt`` and the
    ``keyring`` extra must be satisfied by exactly one pin; the runtime pins
    must be the dev pins at the same versions; and every package in
    ``pnpm-lock.yaml`` must carry an integrity hash. A range in the input files
    that the lock no longer satisfies is a failure, which is what makes a
    bumped constraint fail before a resolver ever sees it.

    The four package pyprojects are read as well: a third-party dependency
    declared there and not pinned is a failure, and so is a VCS or direct-URL
    requirement that is not named with a reason in ``tools/pins/unpinned.txt``.
    That file is how a gap is recorded instead of hidden; a guessed pin would be
    worse than the gap.

    Every ``FROM`` in a Dockerfile must name a ``sha256`` digest, and its comment
    must record the tag the digest came from, so a human can move it.

``--full``
    Create a fresh virtual environment, install a lock with
    ``--require-hashes``, and compare ``pip freeze`` against the pins. A
    missing pin, an extra distribution or a version that moved is drift and
    exits non-zero. ``--offline`` installs from ``$EZCHAIN_WHEELHOUSE`` with
    ``--no-index``, so the full check also runs without a network.

Usage:
    python3.11 tools/check_pins.py --fast
    python3.11 tools/check_pins.py --full
    python3.11 tools/check_pins.py --full --offline --runtime

Exit status is 0 only when every check passes.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from packaging.markers import Marker
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]

PIN_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[(?P<extras>[^\]]*)\])?"
    r"\s*==\s*(?P<version>[^;\s]+)"
    r"\s*(?:;\s*(?P<marker>[^\\]+))?\s*$"
)
HASH_RE = re.compile(r"--hash=sha256:(?P<digest>[0-9a-f]{64})(?:\s|\\)*$")
PNPM_RESOLUTION_RE = re.compile(r"^\s{4}resolution:\s*\{(?P<body>.*)\}\s*$")


class PinsError(RuntimeError):
    """A failure meant for the operator, already formatted."""


@dataclass(frozen=True)
class Pin:
    name: str
    version: str
    hashes: tuple[str, ...]
    marker: str | None

    def applies_here(self) -> bool:
        """Whether the pin is for this interpreter and platform."""
        if self.marker is None:
            return True
        return bool(Marker(self.marker).evaluate())


def _requirement_lines(path: Path) -> list[str]:
    """The installable lines of a pip requirements file, comments removed."""
    if not path.is_file():
        raise PinsError(f"{path} is missing")
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #", 1)[0].strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def _extra_requirements(path: Path, extra: str) -> list[str]:
    """One optional-dependency group of a package's pyproject.toml."""
    if not path.is_file():
        raise PinsError(f"{path} is missing")
    project = tomllib.loads(path.read_text(encoding="utf-8")).get("project", {})
    extras = project.get("optional-dependencies", {})
    if extra not in extras:
        raise PinsError(f"{path} declares no {extra!r} extra")
    return list(extras[extra])


SOURCES: dict[str, Callable[[Path], list[str]]] = {
    "requirements.lock": lambda root: _requirement_lines(root / "requirements.txt"),
    "requirements-runtime.lock": lambda root: _requirement_lines(root / "requirements-runtime.txt"),
    "requirements-keyring.lock": lambda root: _extra_requirements(
        root / "apps" / "api" / "pyproject.toml", "keyring"
    ),
}


def parse_lock(path: Path) -> dict[str, Pin]:
    """Parse a pip-compile lock into pins, refusing anything not exactly pinned.

    A pin is a top-level line (no leading whitespace) of the form
    ``name[extras]==version``; its ``--hash=`` continuations belong to it.
    """
    if not path.is_file():
        raise PinsError(f"{path.name} is missing")
    pins: dict[str, Pin] = {}
    current: str | None = None
    hashes: dict[str, list[str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[0].isspace():
            match = HASH_RE.match(raw.strip())
            if match is None:
                raise PinsError(f"{path.name}: unrecognised continuation line {raw!r}")
            if current is None:
                raise PinsError(f"{path.name}: hash line before any pin: {raw!r}")
            hashes[current].append(match.group("digest"))
            continue
        line = raw.rstrip()
        if line.endswith("\\"):
            line = line[:-1].rstrip()
        match = PIN_RE.match(line)
        if match is None:
            raise PinsError(
                f"{path.name}: {raw!r} is not an exact pin "
                "(expected name[extras]==version[; marker])"
            )
        try:
            Version(match.group("version"))
        except InvalidVersion as exc:
            raise PinsError(f"{path.name}: {raw!r} has a version that is not PEP 440") from exc
        name = canonicalize_name(match.group("name"))
        if name in pins:
            raise PinsError(f"{path.name}: {name} is pinned twice")
        marker = (match.group("marker") or "").strip() or None
        pins[name] = Pin(name, match.group("version"), (), marker)
        hashes[name] = []
        current = name

    return {
        name: Pin(pin.name, pin.version, tuple(hashes[name]), pin.marker)
        for name, pin in pins.items()
    }


def _check_lock(root: Path, lock_name: str, errors: list[str]) -> dict[str, Pin]:
    """One lock: exact, hashed, and covering every requirement it was made from."""
    try:
        pins = parse_lock(root / lock_name)
    except PinsError as exc:
        errors.append(str(exc))
        return {}
    if not pins:
        errors.append(f"{lock_name}: no pins")
        return {}
    for entry in pins.values():
        if not entry.hashes:
            errors.append(f"{lock_name}: {entry.name}=={entry.version} carries no sha256 hash")
    try:
        requirements = SOURCES[lock_name](root)
    except PinsError as exc:
        errors.append(str(exc))
        return pins
    if not requirements:
        errors.append(f"{lock_name}: its source declares no requirements")
    for line in requirements:
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            errors.append(f"{lock_name}: source line {line!r} is not a valid requirement")
            continue
        name = canonicalize_name(requirement.name)
        pin = pins.get(name)
        if pin is None:
            errors.append(f"{lock_name}: {name} is required but not pinned")
            continue
        if requirement.specifier and Version(pin.version) not in requirement.specifier:
            errors.append(
                f"{lock_name}: {name}=={pin.version} does not satisfy {requirement.specifier}"
            )
    return pins


def _check_runtime_subset(dev: dict[str, Pin], runtime: dict[str, Pin], errors: list[str]) -> None:
    """The runtime image must install the same versions the tests were run against."""
    for name, pin in runtime.items():
        dev_pin = dev.get(name)
        if dev_pin is None:
            errors.append(f"requirements-runtime.lock: {name} is missing from requirements.lock")
        elif dev_pin.version != pin.version:
            errors.append(
                f"requirements-runtime.lock: {name}=={pin.version} but "
                f"requirements.lock pins {dev_pin.version}"
            )


# Every package that declares requirements, relative to the root. The workspace packages
# (`ezchain-*`) are installed editable from this checkout, so they are not dependencies to
# pin; everything else a pyproject declares must appear in a lock.
PACKAGE_PYPROJECTS = (
    "packages/core-py/pyproject.toml",
    "packages/adapters/pyproject.toml",
    "packages/sdk-py/pyproject.toml",
    "apps/api/pyproject.toml",
    "services/engine/pyproject.toml",
    "services/executor/pyproject.toml",
    "services/ingest/pyproject.toml",
)
WORKSPACE_PREFIX = "ezchain-"
UNPINNED_PATH = Path("tools") / "pins" / "unpinned.txt"
DOCKERFILES = ("apps/api/Dockerfile", "apps/web/Dockerfile")
MAX_DOCKERFILE_DEPTH = 4
FROM_RE = re.compile(r"^FROM\s+(?P<image>[^\s]+)(?:\s+AS\s+(?P<stage>\S+))?\s*$", re.IGNORECASE)
DIGEST_RE = re.compile(r"@sha256:(?P<digest>[0-9a-f]{64})$")
TAG_DIGEST_COMMENT_RE = re.compile(
    r"^(?P<tag>[\w./-]+:[\w.-]+)\s*->\s*sha256:(?P<digest>[0-9a-f]{64})$"
)


def load_unpinned(path: Path) -> dict[str, str]:
    """Declared-but-unpinnable dependency -> the reason. A reason is mandatory."""
    gaps: dict[str, str] = {}
    if not path.is_file():
        return gaps
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[1].strip():
            raise PinsError(f"{path.name}:{number}: the entry needs a name and a reason")
        gaps[canonicalize_name(parts[0])] = parts[1].strip()
    return gaps


def declared_requirements(root: Path) -> list[tuple[str, str]]:
    """Every third-party requirement a package pyproject declares, with its origin."""
    declared: list[tuple[str, str]] = []
    for relative in PACKAGE_PYPROJECTS:
        path = root / relative
        if not path.is_file():
            continue
        project = tomllib.loads(path.read_text(encoding="utf-8")).get("project", {})
        groups = [("dependencies", project.get("dependencies") or [])]
        for extra, lines in (project.get("optional-dependencies") or {}).items():
            groups.append((f"optional-dependencies.{extra}", list(lines)))
        for group, lines in groups:
            declared.extend((f"{relative}[{group}]", line) for line in lines)
    return declared


def _check_declared_are_pinned(root: Path, pins: dict[str, Pin], errors: list[str]) -> None:
    """A dependency a package declares must be a pin, or a gap with a written reason."""
    try:
        gaps = load_unpinned(root / UNPINNED_PATH)
    except PinsError as exc:
        errors.append(str(exc))
        return
    declared = declared_requirements(root)
    declared_names: set[str] = set()
    for origin, line in declared:
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            errors.append(f"{origin}: {line!r} is not a valid requirement")
            continue
        name = canonicalize_name(requirement.name)
        declared_names.add(name)
        if name.startswith(WORKSPACE_PREFIX):
            continue
        if requirement.url is not None:
            if name not in gaps:
                errors.append(
                    f"{origin}: {name} is a URL requirement with no hash; name it with a "
                    f"reason in {UNPINNED_PATH.as_posix()} or publish a pinned release"
                )
            continue
        if name in gaps:
            errors.append(
                f"{origin}: {name} is named unpinned in {UNPINNED_PATH.as_posix()} but is "
                "declared without a URL; pin it instead"
            )
            continue
        pin = pins.get(name)
        if pin is None:
            errors.append(f"{origin}: {name} is declared but no lock pins it")
            continue
        if requirement.specifier and Version(pin.version) not in requirement.specifier:
            errors.append(
                f"{origin}: {name}=={pin.version} does not satisfy {requirement.specifier}"
            )
    for orphan in sorted(set(gaps) - declared_names):
        errors.append(f"{UNPINNED_PATH.as_posix()}: {orphan} is not declared by any package")


def _documented_tag(comments: list[str], digest: str) -> str | None:
    """The tag a digest came from, read from any ``repo:tag -> sha256:<digest>`` comment."""
    for raw in comments:
        line = raw.lstrip("#").strip()
        match = TAG_DIGEST_COMMENT_RE.match(line)
        if match is not None and match.group("digest") == digest:
            return match.group("tag")
    return None


def base_images(path: Path) -> list[tuple[int, str]]:
    """Every ``FROM`` that names an image, with its line number.

    ``FROM builder AS second`` reuses an earlier stage and is not a base image, so a
    multi-stage file is not asked to pin its own stage names.
    """
    found: list[tuple[int, str]] = []
    stages: set[str] = set()
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = FROM_RE.match(raw.strip())
        if match is None:
            continue
        if match.group("stage") is not None:
            stages.add(match.group("stage").lower())
        image = match.group("image")
        if image.lower() in stages or image.split("@", 1)[0] == "scratch":
            continue
        found.append((number, image))
    return found


def _check_dockerfiles(root: Path, errors: list[str]) -> None:
    """Every base image is pinned by digest and records the tag that digest came from."""
    for relative in DOCKERFILES:
        path = root / relative
        if not path.is_file():
            errors.append(f"{relative} is missing")
            continue
        comments = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("#")
        ]
        for number, image in base_images(path):
            digest_match = DIGEST_RE.search(image)
            if digest_match is None:
                errors.append(f"{relative}:{number}: {image} is not pinned by sha256 digest")
            elif _documented_tag(comments, digest_match.group("digest")) is None:
                errors.append(
                    f"{relative}:{number}: no comment records the tag "
                    f"sha256:{digest_match.group('digest')} came from "
                    "(expected a `repo:tag -> sha256:<digest>` line in the file)"
                )


def _check_pnpm(root: Path, errors: list[str]) -> None:
    """Every resolved package in the Node lock must carry an integrity hash."""
    path = root / "pnpm-lock.yaml"
    if not path.is_file():
        errors.append("pnpm-lock.yaml is missing")
        return
    text = path.read_text(encoding="utf-8")
    if not re.search(r"(?m)^lockfileVersion: '\d+\.\d+'$", text):
        errors.append("pnpm-lock.yaml: no lockfileVersion")
    resolutions = 0
    for line in text.splitlines():
        match = PNPM_RESOLUTION_RE.match(line)
        if match is None:
            continue
        resolutions += 1
        if "integrity: sha" not in match.group("body"):
            errors.append(f"pnpm-lock.yaml: a resolution has no integrity hash: {line.strip()}")
    if resolutions == 0:
        errors.append("pnpm-lock.yaml: no package resolutions")


def check(root: Path) -> list[str]:
    """The fast path: everything provable without installing anything."""
    errors: list[str] = []
    locks = {name: _check_lock(root, name, errors) for name in SOURCES}
    _check_runtime_subset(
        locks.get("requirements.lock", {}), locks.get("requirements-runtime.lock", {}), errors
    )
    _check_pnpm(root, errors)
    _check_dockerfiles(root, errors)
    if (root / "packages").is_dir():
        # Every lock together: a declared dependency may legitimately live in an extra's
        # own lock (the keyring extra), and that is still a pin.
        pinned: dict[str, Pin] = {}
        for entries in locks.values():
            pinned.update(entries)
        _check_declared_are_pinned(root, pinned, errors)
    return errors


def drift(lock_name: str, pins: dict[str, Pin], freeze: str, errors: list[str]) -> None:
    """Compare an installed environment's ``pip freeze`` against the pins."""
    installed: dict[str, str] = {}
    for raw in freeze.splitlines():
        line = raw.strip()
        if not line or line.startswith("-e ") or "@" in line:
            continue
        name, _, version = line.partition("==")
        if not version:
            errors.append(f"freeze: {line!r} is not name==version")
            continue
        installed[canonicalize_name(name)] = version

    for name, pin in sorted(pins.items()):
        if not pin.applies_here():
            continue
        found = installed.get(name)
        if found is None:
            errors.append(f"drift: {name}=={pin.version} is pinned but not installed")
        elif found != pin.version:
            errors.append(f"drift: {name}=={found} installed, pin is {pin.version}")

    for name, version in sorted(installed.items()):
        entry = pins.get(name)
        if entry is None:
            errors.append(f"drift: {name}=={version} is installed but not pinned")
        elif not entry.applies_here():
            errors.append(
                f"drift: {name}=={version} is installed but its pin is for another platform"
            )


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True)


def full(root: Path, lock_name: str, wheelhouse_dir: Path | None) -> int:
    """Install a lock into a fresh venv and require the result to match it."""
    errors = check(root)
    if errors:
        for error in errors:
            print(f"pins: {error}", file=sys.stderr)
        return 1
    pins = parse_lock(root / lock_name)

    wheelhouse = wheelhouse_dir
    if wheelhouse is not None and not wheelhouse.is_dir():
        print(f"pins: wheelhouse {wheelhouse} is not a directory", file=sys.stderr)
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="ezchain-pins-"))
    try:
        venv = workdir / "venv"
        created = _run([sys.executable, "-m", "venv", str(venv)], cwd=workdir)
        if created.returncode != 0:
            print(f"pins: venv creation failed\n{created.stderr}", file=sys.stderr)
            return 1
        python = venv / "bin" / "python"
        if not python.is_file():
            python = venv / "Scripts" / "python.exe"
        install = [
            str(python),
            "-m",
            "pip",
            "install",
            "--require-hashes",
            "--disable-pip-version-check",
            "--quiet",
            "-r",
            str(root / lock_name),
        ]
        if wheelhouse is not None:
            install += ["--no-index", "--find-links", str(wheelhouse)]
        installed = _run(install, cwd=root)
        if installed.returncode != 0:
            source = "wheelhouse" if wheelhouse is not None else "an index"
            print(f"pins: install of {lock_name} from {source} failed\n{installed.stderr}")
            return 1
        frozen = _run([str(python), "-m", "pip", "freeze", "--exclude-editable"], cwd=root)
        if frozen.returncode != 0:
            print(f"pins: pip freeze failed\n{frozen.stderr}", file=sys.stderr)
            return 1
        errors = []
        drift(lock_name, pins, frozen.stdout, errors)
        if errors:
            for error in errors:
                print(f"pins: {error}", file=sys.stderr)
            return 1
        smoke = _run([str(python), "-c", "import fastapi, pydantic, sqlalchemy, yaml"], cwd=root)
        if smoke.returncode != 0:
            print(f"pins: the installed set does not import\n{smoke.stderr}", file=sys.stderr)
            return 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"pins: ok ({len(pins)} pinned, {len(pins)} installed from {lock_name})")
    return 0


def fast(root: Path) -> int:
    """Report the static verdict for the committed pins."""
    errors = check(root)
    if errors:
        for error in errors:
            print(f"pins: {error}", file=sys.stderr)
        return 1
    counts = ", ".join(
        f"{name} {len(parse_lock(root / name))}" for name in SOURCES if (root / name).is_file()
    )
    print(f"pins: ok — exact, hashed and covering their sources ({counts}; node locked)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fast", action="store_true", help="static checks only (default)")
    mode.add_argument("--full", action="store_true", help="install into a fresh venv and diff")
    parser.add_argument("--offline", action="store_true", help="install from EZCHAIN_WHEELHOUSE")
    parser.add_argument("--runtime", action="store_true", help="install the runtime lock")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root to check")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.full:
            lock = "requirements-runtime.lock" if args.runtime else "requirements.lock"
            wheelhouse = None
            if args.offline:
                raw = os.environ.get("EZCHAIN_WHEELHOUSE")
                if not raw:
                    print(
                        "pins: --offline needs EZCHAIN_WHEELHOUSE pointing at a directory "
                        "of wheels downloaded with the same lock "
                        "(tools/pins/install.sh --wheelhouse DIR fills it)",
                        file=sys.stderr,
                    )
                    return 2
                wheelhouse = Path(raw)
            return full(root, lock, wheelhouse)
        return fast(root)
    except PinsError as exc:
        print(f"pins: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
