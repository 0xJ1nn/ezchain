#!/usr/bin/env python3
"""Inventory dependency licences from the locks and the installed metadata.

A dependency can reach the product through Python, Node or Rust. The committed
locks say which versions were chosen; installed metadata is where the licence
is declared. This check joins the two into one inventory and fails the local
gate when a declaration is disallowed, unrecognised or empty, or when a newly
locked dependency has no declaration at all. It reads files only: no resolver,
no registry, no network and no telemetry.

Usage:
    python3 tools/check-licences.py                 # check this checkout
    python3 tools/check-licences.py --root DIR      # check another checkout
    python3 tools/check-licences.py --json          # the inventory as JSON
    python3 tools/check-licences.py --inventory F   # write that JSON to F
    python3 tools/check-licences.py --fixture PATH  # include an extra declaration

The inventory is the union of the locks and the declarations it can read. Each
row carries an ecosystem, name, version, licence and the path that declared the
licence; a row whose declaration is missing carries an explicit UNKNOWN value
rather than a guess. The check is strict about what it can see: a declaration
that names a strong-copyleft, non-commercial or unrecognised licence fails with
the exact path of the file that declared it, and so does a declaration with no
licence value. A lock entry with no declaration is an error too, except where a
set is known not to be installed in this checkout: the optional `keyring` lock,
Rust crates with no local source cache, and Node packages that are pinned but
not installed. Those gaps are reported as notes, never treated as a pass for
the missing dependency.

Exit status 0 means every declaration seen was allowed and every locked
dependency without a declared gap had one. Status 1 means at least one
declaration failed; the reasons are on stderr. Status 2 means the check could
not read what it was asked to read.
"""

from __future__ import annotations

import argparse
import email.parser
import json
import os
import re
import site
import sys
import sysconfig
import tomllib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PYTHON_LOCKS = (
    "requirements.lock",
    "requirements-runtime.lock",
    "requirements-keyring.lock",
)
NODE_LOCK = "pnpm-lock.yaml"
RUST_LOCK = "rust/Cargo.lock"
KEYRING_LOCK = "requirements-keyring.lock"
HEADINGS = ("ecosystem", "name", "version", "licence", "declared in")

PIN_RE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*==\s*(?P<version>\S+)")
PNPM_KEY_RE = re.compile(r"^  (?P<key>'[^']+'|[^\s'][^:']*):\s*$")
LICENCE_NOTE_RE = re.compile(r"licen[cs]e\s*:\s*(?P<value>\S.*?)\s*$", re.IGNORECASE)

# Permissive and weak-copyleft licences already reviewed in the pinned set. Strong
# copyleft, source-available and non-commercial terms are not on this list: adding
# one is a review, not a silent pass through a lockfile.
ALLOWED_TOKENS = frozenset(
    {
        "0bsd",
        "apache-2.0",
        "artistic-2.0",
        "blueoak-1.0.0",
        "bsd-2-clause",
        "bsd-3-clause",
        "bsl-1.0",
        "cc-by-4.0",
        "cc0-1.0",
        "curl",
        "isc",
        "lgpl-2.1-only",
        "lgpl-2.1-or-later",
        "lgpl-3.0-only",
        "lgpl-3.0-or-later",
        "llvm-exception",
        "mit",
        "mpl-2.0",
        "openssl",
        "postgresql",
        "psf-2.0",
        "python-2.0",
        "unicode-3.0",
        "unicode-dfs-2016",
        "unlicense",
        "wtfpl",
        "zlib",
    }
)
ALIASES = {
    "the mit license (mit)": "mit",
    "mit license": "mit",
    "apache software license": "apache-2.0",
    "apache license 2.0": "apache-2.0",
    "apache 2.0": "apache-2.0",
    "bsd license": "bsd-3-clause",
    "new bsd license": "bsd-3-clause",
    "mozilla public license 2.0 (mpl 2.0)": "mpl-2.0",
    "mozilla public license 2.0": "mpl-2.0",
    "python software foundation license": "psf-2.0",
    "gnu lesser general public license v3": "lgpl-3.0-only",
    "gnu lesser general public license v3 or later": "lgpl-3.0-or-later",
    "isc license": "isc",
}
DENIED_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bagpl\b|\bagpl-",
        r"(?<!l)\bgpl\b|\bgpl-[23]",
        r"\bsspl\b",
        r"\bepl-[12]",
        r"\bcc-by-sa\b",
        r"commons.?clause",
        r"non.?commercial",
        r"\bbusl\b",
        r"business source license",
        r"\bproprietary\b",
        r"\bunlicensed\b",
        r"all rights reserved",
        r"\belastic license\b",
        r"\bpolyform\b",
    )
)
UNKNOWN = "UNKNOWN"


class LicenceError(RuntimeError):
    """A file the check needs could not be read."""


@dataclass(frozen=True)
class Locked:
    """One dependency named by a committed lock, with the locks that name it."""

    ecosystem: str
    name: str
    version: str
    locks: tuple[str, ...]


@dataclass(frozen=True)
class Declaration:
    """A licence value and the exact file that declared it."""

    ecosystem: str
    name: str
    version: str
    licence: str
    path: Path
    from_fixture: bool = False


Inline = dict[str, dict[str, list[Declaration]]]


@dataclass(frozen=True)
class Entry:
    """One row of the inventory."""

    ecosystem: str
    name: str
    version: str
    licence: str
    declared_in: str
    locks: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Report:
    inventory: tuple[Entry, ...]
    errors: tuple[str, ...]
    notes: tuple[str, ...]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise LicenceError(f"licences: cannot read {path}: {exc}") from exc


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip("-").lower()


def key_for(ecosystem: str, name: str) -> str:
    """Distribution names differ in case and punctuation between metadata and locks."""
    return canonical(name) if ecosystem == "python" else name


def licence_status(value: str) -> tuple[str, str]:
    """Classify a declared licence as ok, disallowed, unrecognised or missing."""
    raw = value.strip()
    if not raw or raw.upper() in {"UNKNOWN", "N/A", "NONE"}:
        return "missing", "no licence value"
    first = raw.splitlines()[0].strip()
    if len(first) > 120:
        text = first.lower()
        if "mit" in text:
            first = "MIT"
        elif "apache" in text:
            first = "Apache-2.0"
        elif "mozilla" in text:
            first = "MPL-2.0"
        else:
            return "unrecognised", f"unrecognised licence {first[:80]!r}"
    first = ALIASES.get(first.lower(), first)
    text = first.replace("/", " or ").lower()
    text = re.sub(r"\s+", " ", text)
    for ring in (" or ", " and ", " with ", ","):
        text = text.replace(ring, "\n")
    tokens = []
    for part in text.split("\n"):
        token = part.strip().strip("() ")
        if token:
            tokens.append(ALIASES.get(token, token))
    if not tokens:
        return "missing", "no licence value"
    for pattern in DENIED_PATTERNS:
        for token in tokens:
            if pattern.search(token):
                return "disallowed", f"disallowed licence {first!r}"
    unknown = [token for token in tokens if token not in ALLOWED_TOKENS]
    if unknown:
        return "unrecognised", f"unrecognised licence {first!r}"
    return "ok", first


def _add_inline(inline: Inline, declaration: Declaration) -> None:
    """Attach a lockfile licence note to the package it annotates."""
    group = inline.setdefault(declaration.ecosystem, {})
    group.setdefault(declaration.name, []).append(declaration)


def parse_python_lock(path: Path, inline: Inline) -> list[Locked]:
    """Name, version and inline licence notes from a pip-compile lock."""
    if not path.is_file():
        return []
    locked: dict[str, Locked] = {}
    current: str | None = None
    for number, raw in enumerate(_read(path).splitlines(), start=1):
        line = raw.split(" #", 1)[0].strip()
        match = PIN_RE.match(line)
        if match and not raw[0].isspace():
            name = canonical(match.group("name"))
            current = name
            locked[name] = Locked("python", name, match.group("version"), (path.name,))
        note = LICENCE_NOTE_RE.search(raw)
        if note and current is not None:
            version = locked[current].version
            declared = Declaration(
                "python", current, version, note.group("value"), Path(f"{path}:{number}")
            )
            _add_inline(inline, declared)
    return list(locked.values())


def _split_node_key(key: str) -> tuple[str, str]:
    """`'@scope/name@1.2.3(peer@9)'` -> name and the bare version."""
    name, _, version = key.rpartition("@")
    version = re.sub(r"\(.*$", "", version).strip()
    return name, version


def parse_node_lock(path: Path, inline: Inline) -> list[Locked]:
    """Name and version from the `packages` keys of a pnpm v9 lock."""
    if not path.is_file():
        return []
    locked: dict[tuple[str, str], Locked] = {}
    in_packages = False
    current: tuple[str, str] | None = None
    for raw in _read(path).splitlines():
        if raw and not raw[0].isspace():
            in_packages = raw.startswith("packages:")
            current = None
            continue
        if not in_packages:
            continue
        key = PNPM_KEY_RE.match(raw)
        if key:
            name, version = _split_node_key(key.group("key").strip("'"))
            current = (name, version)
            locked[current] = Locked("node", name, version, (path.name,))
            continue
        note = LICENCE_NOTE_RE.search(raw)
        if note and current is not None:
            name, version = current
            declared = Declaration("node", name, version, note.group("value"), path)
            _add_inline(inline, declared)
    return list(locked.values())


def parse_rust_lock(path: Path) -> list[Locked]:
    """Registry crates named by Cargo.lock; workspace crates are our own code."""
    if not path.is_file():
        return []
    try:
        document = tomllib.loads(_read(path))
    except tomllib.TOMLDecodeError as exc:
        raise LicenceError(f"licences: {path} is not valid TOML: {exc}") from exc
    packages = document.get("package", [])
    if not isinstance(packages, list):
        return []
    locked: list[Locked] = []
    for package in packages:
        if not isinstance(package, dict) or not package.get("source"):
            continue
        name = str(package.get("name", ""))
        version = str(package.get("version", ""))
        if name and version:
            locked.append(Locked("rust", name, version, (path.name,)))
    return locked


def locked_dependencies(root: Path, inline: Inline) -> list[Locked]:
    """The union of every committed lock, keyed per ecosystem."""
    found: dict[tuple[str, str, str], set[str]] = {}
    for lock_name in PYTHON_LOCKS:
        for item in parse_python_lock(root / lock_name, inline):
            found.setdefault((item.ecosystem, item.name, item.version), set()).update(item.locks)
    for item in parse_node_lock(root / NODE_LOCK, inline):
        found.setdefault((item.ecosystem, item.name, item.version), set()).update(item.locks)
    for item in parse_rust_lock(root / RUST_LOCK):
        found.setdefault((item.ecosystem, item.name, item.version), set()).update(item.locks)
    return [
        Locked(ecosystem, name, version, tuple(sorted(locks)))
        for (ecosystem, name, version), locks in sorted(found.items())
    ]


def _metadata_directories(root: Path) -> set[Path]:
    """Interpreter site-packages plus a checkout's venv, without duplicates."""
    candidates = set(site.getsitepackages())
    candidates.add(site.getusersitepackages())
    purelib = sysconfig.get_paths().get("purelib")
    if purelib:
        candidates.add(purelib)
    candidates.update(str(path) for path in root.glob(".venv/lib/python*/site-packages"))
    return {
        Path(candidate).resolve()
        for candidate in candidates
        if candidate and Path(candidate).is_dir()
    }


def _python_declaration(path: Path) -> Declaration:
    message = email.parser.Parser().parsestr(_read(path), headersonly=True)
    name = message.get("Name") or path.parent.name.split("-")[0]
    version = message.get("Version") or path.parent.name[len(name) + 1 : -len(".dist-info")]
    licence = message.get("License-Expression") or message.get("License") or ""
    if not licence.strip():
        for classifier in message.get_all("Classifier") or []:
            if classifier.startswith("License ::"):
                licence = classifier.split("::")[-1].strip()
                break
    return Declaration("python", name, version, licence.strip(), path)


def python_declarations(root: Path, locked_names: set[str]) -> list[Declaration]:
    """Read METADATA for locked distributions in site-packages and under the root."""
    results: list[Declaration] = []
    seen: set[Path] = set()
    for base in _metadata_directories(root):
        for metadata in sorted(base.glob("*.dist-info/METADATA")):
            if metadata.resolve() in seen:
                continue
            seen.add(metadata.resolve())
            declaration = _python_declaration(metadata)
            if canonical(declaration.name) in locked_names:
                results.append(declaration)
    skipped = {".venv", "node_modules", ".git", "target"}
    for metadata in sorted(root.rglob("*.dist-info/METADATA")):
        if skipped.intersection(metadata.relative_to(root).parts):
            continue
        if metadata.resolve() in seen:
            continue
        seen.add(metadata.resolve())
        results.append(_python_declaration(metadata))
    return results


def _is_package_root(path: Path) -> bool:
    parent = path.parent
    if parent.parent.name == "node_modules":
        return True
    return parent.parent.name.startswith("@") and parent.parent.parent.name == "node_modules"


def _node_value(document: dict[str, object]) -> str:
    value = document.get("license") or document.get("licences") or ""
    if isinstance(value, dict):
        value = value.get("type", "")
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    return str(value)


def node_declarations(root: Path) -> list[Declaration]:
    """Read the root manifest of each installed Node package, not its subpath files."""
    results: list[Declaration] = []
    for path in sorted(root.rglob("package.json")):
        if not _is_package_root(path):
            continue
        try:
            document = json.loads(_read(path))
        except json.JSONDecodeError as exc:
            raise LicenceError(f"licences: {path} is not valid JSON: {exc}") from exc
        if not isinstance(document, dict):
            continue
        name = str(document.get("name", ""))
        version = str(document.get("version", ""))
        if name:
            results.append(Declaration("node", name, version, _node_value(document), path))
    return results


def rust_declarations(root: Path, locked: set[tuple[str, str]]) -> list[Declaration]:
    """Read licence fields from cached crate sources for crates in Cargo.lock."""
    cargo_home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))
    results: list[Declaration] = []
    seen: set[tuple[str, str]] = set()
    for manifest in sorted(cargo_home.glob("registry/src/*/*/Cargo.toml")):
        try:
            document = tomllib.loads(_read(manifest))
        except tomllib.TOMLDecodeError as exc:
            raise LicenceError(f"licences: {manifest} is not valid TOML: {exc}") from exc
        package = document.get("package")
        if not isinstance(package, dict):
            continue
        name = str(package.get("name", ""))
        version = str(package.get("version", ""))
        key = (name, version)
        if key not in locked or key in seen:
            continue
        seen.add(key)
        licence = str(package.get("license") or "")
        licence_file = package.get("license-file")
        if not licence and licence_file and (manifest.parent / str(licence_file)).is_file():
            licence = f"see {licence_file}"
        results.append(Declaration("rust", name, version, licence, manifest))
    return results


def _node_fixture(path: Path) -> Declaration | None:
    try:
        document = json.loads(_read(path))
    except json.JSONDecodeError as exc:
        raise LicenceError(f"licences: {path} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        return None
    return Declaration(
        "node",
        str(document.get("name", "")),
        str(document.get("version", "")),
        _node_value(document),
        path,
        from_fixture=True,
    )


def _rust_fixture(path: Path) -> Declaration | None:
    try:
        document = tomllib.loads(_read(path))
    except tomllib.TOMLDecodeError as exc:
        raise LicenceError(f"licences: {path} is not valid TOML: {exc}") from exc
    package = document.get("package")
    if not isinstance(package, dict):
        return None
    return Declaration(
        "rust",
        str(package.get("name", "")),
        str(package.get("version", "")),
        str(package.get("license") or ""),
        path,
        from_fixture=True,
    )


def root_declarations(root: Path) -> list[Declaration]:
    """A licence at the root of a tree is a declaration; a bare workspace manifest is not."""
    results: list[Declaration] = []
    package = root / "package.json"
    if package.is_file():
        declaration = _node_fixture(package)
        if declaration and declaration.licence:
            results.append(declaration)
    for name, parser in (("METADATA", _python_declaration), ("Cargo.toml", _rust_fixture)):
        path = root / name
        if not path.is_file():
            continue
        declaration = parser(path)
        if declaration and declaration.licence:
            results.append(replace(declaration, from_fixture=True))
    return results


def fixture_declarations(paths: tuple[Path, ...], inline: Inline) -> list[Declaration]:
    """Read a file or directory handed in explicitly, whether or not it is locked."""
    results: list[Declaration] = []
    for raw in paths:
        path = raw.resolve()
        if not path.exists():
            raise LicenceError(f"licences: fixture {path} does not exist")
        if path.is_dir():
            root_manifest = path / "package.json"
            if root_manifest.is_file() and not _is_package_root(root_manifest):
                node = _node_fixture(root_manifest)
                if node:
                    results.append(node)
            results.extend(node_declarations(path))
            for metadata in sorted(path.rglob("*.dist-info/METADATA")):
                results.append(replace(_python_declaration(metadata), from_fixture=True))
            for manifest in sorted(path.rglob("Cargo.toml")):
                crate = _rust_fixture(manifest)
                if crate:
                    results.append(crate)
        elif path.name == "package.json":
            package = _node_fixture(path)
            if package:
                results.append(package)
        elif path.name == "METADATA":
            results.append(replace(_python_declaration(path), from_fixture=True))
        elif path.name == "Cargo.toml":
            crate = _rust_fixture(path)
            if crate:
                results.append(crate)
        elif path.name in {*PYTHON_LOCKS, NODE_LOCK}:
            if path.name == NODE_LOCK:
                parse_node_lock(path, inline)
            else:
                parse_python_lock(path, inline)
        else:
            raise LicenceError(f"licences: fixture {path} is not a licence declaration")
    return results


def _documented_gap(item: Locked) -> str:
    """Sets that are pinned but knowingly not installed in this checkout."""
    if item.ecosystem == "python" and item.locks == (KEYRING_LOCK,):
        return "keyring extra is pinned but no deployment installs it"
    if item.ecosystem == "rust":
        return "Rust crate source is not in the local cache"
    if item.ecosystem == "node":
        return "Node package is pinned but not installed in this checkout"
    return ""


def build_report(root: Path, fixtures: tuple[Path, ...] = ()) -> Report:
    """Join the locks and every readable declaration into one sorted inventory."""
    inline: Inline = {}
    locked = locked_dependencies(root, inline)
    locked_python = {item.name for item in locked if item.ecosystem == "python"}
    locked_rust = {(item.name, item.version) for item in locked if item.ecosystem == "rust"}
    declarations = python_declarations(root, locked_python)
    if (root / "node_modules").is_dir():
        declarations += node_declarations(root)
    declarations += rust_declarations(root, locked_rust)
    declarations += root_declarations(root)
    declarations += fixture_declarations(fixtures, inline)

    seen_declarations = {
        (item.ecosystem, key_for(item.ecosystem, item.name)) for item in declarations
    }
    for ecosystem, names in inline.items():
        for name, noted in names.items():
            key = (ecosystem, key_for(ecosystem, name))
            if key not in seen_declarations:
                declarations.append(noted[0])
                seen_declarations.add(key)

    by_key: dict[tuple[str, str], Declaration] = {}
    for declaration in declarations:
        key = (declaration.ecosystem, key_for(declaration.ecosystem, declaration.name))
        by_key.setdefault(key, declaration)

    errors: list[str] = []
    for candidate in declarations:
        status, reason = licence_status(candidate.licence)
        if status != "ok":
            errors.append(
                f"{reason} for {candidate.name} {candidate.version} declared in {candidate.path}"
            )

    inventory: list[Entry] = []
    notes: list[str] = []
    locked_keys = {(item.ecosystem, key_for(item.ecosystem, item.name)) for item in locked}
    seen_fixtures: set[tuple[str, str]] = set()
    for item in locked:
        found = by_key.get((item.ecosystem, key_for(item.ecosystem, item.name)))
        if found is None:
            gap = _documented_gap(item)
            if gap:
                notes.append(gap)
                licence = f"{UNKNOWN} ({gap})"
                source = ""
            else:
                locks = ", ".join(str(root / lock) for lock in item.locks)
                errors.append(
                    f"no licence declaration for {item.name} {item.version} pinned in {locks}"
                )
                licence = UNKNOWN
                source = ""
        else:
            licence = found.licence or UNKNOWN
            source = str(found.path)
        inventory.append(
            Entry(item.ecosystem, item.name, item.version, licence, source, item.locks)
        )
    for declaration in declarations:
        key = (declaration.ecosystem, key_for(declaration.ecosystem, declaration.name))
        if not declaration.from_fixture or key in seen_fixtures or key in locked_keys:
            continue
        seen_fixtures.add(key)
        inventory.append(
            Entry(
                declaration.ecosystem,
                declaration.name,
                declaration.version,
                declaration.licence or UNKNOWN,
                str(declaration.path),
            )
        )
    return Report(tuple(inventory), tuple(errors), tuple(notes))


def render_text(report: Report) -> str:
    """A readable table with one row per dependency, plus the verdict."""
    rows = [
        (item.ecosystem, item.name, item.version, item.licence, item.declared_in)
        for item in report.inventory
    ]
    widths = [
        max(len(column) for column in [heading, *[row[index] for row in rows]])
        for index, heading in enumerate(HEADINGS)
    ]
    lines = [
        "  ".join(value.ljust(width) for value, width in zip(HEADINGS, widths, strict=True)),
        "  ".join("-" * width for width in widths),
    ]
    lines.extend(
        "  ".join(value.ljust(width) for value, width in zip(row, widths, strict=True))
        for row in rows
    )
    lines.append(f"{len(rows)} dependencies inventoried")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path", nargs="?", type=Path, help="checkout root, or a declaration fixture"
    )
    parser.add_argument("--root", type=Path, help="checkout root to inventory")
    parser.add_argument(
        "--fixture", type=Path, action="append", default=[], help="extra declaration"
    )
    parser.add_argument("--json", action="store_true", help="print the inventory as JSON")
    parser.add_argument("--inventory", type=Path, help="write the JSON inventory to this file")
    args = parser.parse_args(argv)

    fixtures = list(args.fixture)
    root = args.root or ROOT
    if args.path is not None:
        if args.path.is_file():
            fixtures.append(args.path)
        else:
            root = args.path
    env_fixture = os.environ.get("EZCHAIN_LICENCE_FIXTURE")
    if env_fixture:
        fixtures.append(Path(env_fixture))
    root = root.resolve()

    try:
        report = build_report(root, tuple(fixtures))
    except LicenceError as exc:
        print(exc, file=sys.stderr)
        return 2

    payload = json.dumps([asdict(item) for item in report.inventory], indent=2, sort_keys=True)
    if args.inventory is not None:
        try:
            args.inventory.write_text(payload + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"licences: cannot write {args.inventory}: {exc}", file=sys.stderr)
            return 2
    print(payload if args.json else render_text(report))
    for note in dict.fromkeys(report.notes):
        print(f"licences: note: {note}", file=sys.stderr)
    for error in report.errors:
        print(f"licences: {error}", file=sys.stderr)
    if report.errors:
        print(f"licences: {len(report.errors)} licence error(s)", file=sys.stderr)
        return 1
    print("licences: ok", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
