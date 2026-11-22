#!/usr/bin/env python3
"""Enforce the language map in ``architecture/language-map.json``.

The map is the machine-readable form of ADR-0050 (language policy), ADR-0052 (what each
language owns) and ADR-0053 (deployment target, freeze, and the enforcement this script
performs). Without this check the map is a document that drifts; with it, a pull request
that puts a Rust-owned layer in Python, adds a fourth language, imports the compiled
bridge from an undeclared module, or lets the browser see the API token fails the gate.

Checks, in the order they run:

1. **Forbidden languages.** A source file in a language the map forbids is a failure.
   Three languages are the budget; a fourth needs its own ADR.
2. **Declared layers.** Every Python package with a ``pyproject.toml`` must be declared
   in ``python_layers``, and every declared path must exist. A new deployable cannot
   appear without a language decision.
3. **Import edges.** A Python layer may import only the packages named in ``may_import``.
   Cycles are not banned (the platform is one process graph) but undeclared edges are,
   and every exception lives in ``known_violations`` with a file list that may only shrink.
4. **Bridge clients.** Only ``python_entry_points`` may import ``ezchain_bridge``. A
   second client is how a fallback appears.
5. **Third-party seams.** ``pumpfire`` is imported only inside the packages that declare
   the optional extra.
6. **Rust crates.** Every crate directory is declared as ``shipping`` or ``frozen``, and a
   frozen crate must not be depended on by a shipping crate unless the map says so.
   The dependency is what makes a freeze real.
7. **Money paths.** No ``float(`` or ``round(`` in the money oracle or the ledger.
8. **Renderer boundary.** No Python or Rust under the UI, and no client component that
   reads the deployment token.

Usage:
    python3 tools/check-language-map.py            # gate: exit 1 on any finding
    python3 tools/check-language-map.py --json     # machine-readable findings
    python3 tools/check-language-map.py --explain  # print the rules, then the findings
    python3 tools/check-language-map.py --root /path/to/ezchain  # check another checkout

Exit status is 0 when clean, 1 on findings, 2 when the map itself cannot be read.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MAP_PATH = ROOT / "architecture" / "language-map.json"
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    "dist",
    "build",
    "target",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
}
# Test trees are allowed to exercise a package the production layer may not import: a
# parity test imports the Python oracle from a Rust-owned layer on purpose. Tooling is
# excluded from the symbol-level checks because a checker legitimately names the symbols
# it looks for; it is not a runtime layer.
TEST_PREFIXES = ("tests/", "test/")
ORACLE_PREFIXES = ("rust/",)
SYMBOL_CHECK_SKIP = ("tools/", "architecture/", "docs/")


class Finding(dict):
    """One violation: enough to fix it without reading the checker."""


def finding(kind: str, where: str, detail: str, fix: str) -> Finding:
    return Finding(kind=kind, where=where, detail=detail, fix=fix)


def read_map() -> dict[str, Any]:
    try:
        return json.loads(MAP_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"language-map: {MAP_PATH} is missing", file=sys.stderr)
        raise SystemExit(2) from None
    except json.JSONDecodeError as exc:
        print(f"language-map: {MAP_PATH} is not valid JSON: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


def walk_visible_files() -> set[str]:
    """Files visible without git: no hidden directories and no nested checkouts.

    A worktree kept inside the checkout has its own ``.git``. Descending into it would
    report its duplicate packages as product source, so hidden directories and any
    directory that is itself a repository are pruned before the walk goes deeper.
    """
    found: set[str] = set()
    for directory, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [
            name
            for name in dirnames
            if not name.startswith(".")
            and name not in SKIP_DIRS
            and not (Path(directory) / name / ".git").exists()
        ]
        for name in filenames:
            found.add(str((Path(directory) / name).relative_to(ROOT)))
    return found


def source_file_set() -> set[str]:
    """The one file set every scan uses: tracked plus untracked-but-not-ignored.

    Git already omits an ignored nested worktree, so asking which files belong to this
    checkout is both faster and stricter than walking the tree. The walk is the fallback
    outside a checkout.
    """
    import subprocess

    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return walk_visible_files()
    return {rel for line in out.splitlines() if (rel := line.strip()) and (ROOT / rel).exists()}


def tracked_files() -> list[Path]:
    """Source-visible files as paths, for callers that want the list."""
    return sorted(Path(path) for path in source_file_set())


def is_test(rel: str) -> bool:
    return rel.startswith(TEST_PREFIXES) or "/tests/" in rel


def is_oracle(rel: str) -> bool:
    return rel.startswith(ORACLE_PREFIXES) and "/tests/" in rel


def is_tooling(rel: str) -> bool:
    return rel.startswith(SYMBOL_CHECK_SKIP)


# --- 1. forbidden languages -------------------------------------------------------------


def check_forbidden_languages(files: list[Path], spec: dict[str, Any]) -> list[Finding]:
    banned = spec["forbidden"]["languages"]["extensions"]
    reason = spec["forbidden"]["languages"]["reason"]
    out = []
    for rel in files:
        if rel.suffix in banned:
            out.append(
                finding(
                    "forbidden-language",
                    str(rel),
                    f"{rel.suffix} is not one of the three languages the project keeps ({reason})",
                    "Write the component in Rust, Python or TypeScript, or write an ADR before "
                    "adding a language.",
                )
            )
    return out


# --- 2. declared layers -----------------------------------------------------------------


def check_declared_layers(spec: dict[str, Any], files: set[str] | None = None) -> list[Finding]:
    declared = spec["python_layers"]
    out = []
    for rel in sorted(source_file_set() if files is None else files):
        path = Path(rel)
        if path.name != "pyproject.toml" or len(path.parts) < 2:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        package_dir = path.parent
        rel_dir = str(package_dir)
        text = (ROOT / path).read_text(encoding="utf-8")
        match = re.search(r'^name\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if not match:
            continue
        module = match.group(1).replace("-", "_")
        # The workspace packages ship as the distributable name but import as the module;
        # map each declared path to the module found in its own pyproject.
        match_declared = next(
            (key for key, value in declared.items() if value["path"] == rel_dir),
            None,
        )
        if match_declared is None:
            out.append(
                finding(
                    "undeclared-layer",
                    rel_dir,
                    f"{module} is a Python package with no entry in the language map",
                    "Add a python_layers entry naming its role, language, allowed imports and "
                    "reason, so the language decision is explicit.",
                )
            )
            continue
        if match_declared != module:
            out.append(
                finding(
                    "layer-name-mismatch",
                    rel_dir,
                    f"the map calls this layer {match_declared!r} but the package is {module!r}",
                    "Keep the map key and the importable module name identical.",
                )
            )
    for key, value in declared.items():
        if not (ROOT / value["path"]).is_dir():
            out.append(
                finding(
                    "missing-layer",
                    value["path"],
                    f"the map declares {key} at a path that does not exist",
                    "Remove the entry or restore the directory.",
                )
            )
    return out


# --- 3/4/5. import edges ----------------------------------------------------------------


def python_files(layers: dict[str, Any], files: set[str] | None = None) -> dict[str, list[Path]]:
    visible = source_file_set() if files is None else files
    by_layer: dict[str, list[Path]] = {}
    for key, value in layers.items():
        base = value["path"]
        by_layer[key] = (
            sorted(
                ROOT / rel for rel in visible if rel.startswith(f"{base}/") and rel.endswith(".py")
            )
            if (ROOT / base).is_dir()
            else []
        )
    return by_layer


def imports_of(path: Path) -> set[str]:
    """The top-level modules a Python file imports, from the AST, not from a regex.

    A commented-out import is not an edge, and an import inside a function still is: the
    boundary is about what the code needs to run.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def check_import_edges(spec: dict[str, Any], files: set[str] | None = None) -> list[Finding]:
    layers = spec["python_layers"]
    by_layer = python_files(layers, files)
    module_to_layer = {}
    for key, value in layers.items():
        text = (ROOT / value["path"] / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^name\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if match:
            module_to_layer[match.group(1).replace("-", "_")] = key

    # An exemption belongs to one recorded edge, not to a whole layer. Keying it by the
    # from-file alone would excuse a listed module for *every* target layer, so a fresh
    # forbidden import in that file could slip through the gate unnoticed.
    exemptions: dict[tuple[str, str], set[str]] = {}
    for entry in spec.get("known_violations", []):
        exemptions.setdefault((entry["from"], entry["to"]), set()).update(entry["files"])

    out = []
    for layer, paths in by_layer.items():
        allowed = set(layers[layer]["may_import"])
        for path in paths:
            rel = str(path.relative_to(ROOT))
            if is_test(rel):
                continue
            for module in imports_of(path):
                target = module_to_layer.get(module)
                if target is None or target == layer:
                    continue
                if module in allowed:
                    continue
                if rel in exemptions.get((layer, target), set()):
                    continue
                out.append(
                    finding(
                        "undeclared-import",
                        f"{rel}:{target}",
                        f"{layer} imports {module}, which the map does not allow",
                        f"Add {module!r} to {layer}.may_import if the dependency is correct, or "
                        "move the shared code down a layer. Do not widen the list to silence "
                        "the check.",
                    )
                )
    return out


def imports_bridge(path: Path) -> bool:
    """True when the module actually reaches the extension, not merely names it in prose.

    A docstring that explains why a module does *not* call the bridge is not a client. The
    two shapes that are: a static import, and ``importlib.import_module`` with the literal
    name (the shape the real clients use, because a missing wheel must raise at call time).
    """
    if "ezchain_bridge" not in path.read_text(encoding="utf-8", errors="ignore"):
        return False
    if "ezchain_bridge" in imports_of(path):
        return True
    return bool(
        re.search(
            r"import_module\(\s*[\"']ezchain_bridge[\"']",
            path.read_text(encoding="utf-8", errors="ignore"),
        )
    )


def check_bridge_clients(spec: dict[str, Any], files: set[str] | None = None) -> list[Finding]:
    allowed = {entry["path"] for entry in spec["python_entry_points"]}
    out = []
    for rel in sorted(source_file_set() if files is None else files):
        if not rel.endswith(".py"):
            continue
        if any(part in SKIP_DIRS for part in Path(rel).parts):
            continue
        path = ROOT / rel
        if is_test(rel) or is_oracle(rel) or is_tooling(rel):
            continue
        if not imports_bridge(path):
            continue
        if rel in allowed:
            continue
        out.append(
            finding(
                "undeclared-bridge-client",
                rel,
                "this module talks to the compiled bridge, and it is not a declared client",
                "Route the call through an existing client (apps/api/src/ezchain_api/native.py "
                "or services/executor/src/ezchain_executor/native.py). A second client is how a "
                "silent Python fallback reappears.",
            )
        )
    return out


def check_third_party_seams(spec: dict[str, Any], files: set[str] | None = None) -> list[Finding]:
    out = []
    visible = source_file_set() if files is None else files
    for module, rule in spec.get("third_party_seams", {}).items():
        allowed = rule["allowed"]
        for rel in sorted(visible):
            if not rel.endswith(".py"):
                continue
            if any(part in SKIP_DIRS for part in Path(rel).parts):
                continue
            path = ROOT / rel
            if is_test(rel) or is_oracle(rel) or is_tooling(rel):
                continue
            in_seam = any(rel.startswith(prefix + "/") for prefix in allowed)
            if not in_seam:
                if module in path.read_text(encoding="utf-8", errors="ignore"):
                    out.append(
                        finding(
                            "third-party-seam",
                            rel,
                            f"{module} is imported outside its declared packages "
                            f"({', '.join(allowed)})",
                            "Keep the optional dependency behind the package that declares the "
                            "extra, so the API image and the desktop bundle stay free of it.",
                        )
                    )
    return out


# --- 6. rust crates ---------------------------------------------------------------------


def check_rust_crates(spec: dict[str, Any], files: set[str] | None = None) -> list[Finding]:
    visible = source_file_set() if files is None else files
    out = []
    shipping = set(spec["rust_crates"]["shipping"])
    frozen = set(spec["rust_crates"]["frozen"])
    unwired = set(spec["rust_crates"].get("shipping_unwired", {}))
    declared = shipping | frozen | unwired
    crates = {}
    for rel in visible:
        parts = Path(rel).parts
        if (
            len(parts) == 3
            and parts[0] == "rust"
            and parts[1].startswith("ezchain-")
            and parts[2] == "Cargo.toml"
        ):
            crates[parts[1]] = ROOT / "rust" / parts[1]
    for name in crates:
        if name not in declared:
            out.append(
                finding(
                    "undeclared-crate",
                    f"rust/{name}",
                    "a Rust crate is neither shipping nor frozen",
                    "Declare it in rust_crates.shipping with a user, or in rust_crates.frozen "
                    "with the reason nothing links it.",
                )
            )
    for name in declared - set(crates):
        out.append(
            finding(
                "missing-crate",
                f"rust/{name}",
                "the map declares a crate that is not in the workspace",
                "Remove the entry.",
            )
        )
    # A frozen crate pulled in by a shipping crate is a freeze in name only.
    for name in sorted(shipping & set(crates)):
        text = (crates[name] / "Cargo.toml").read_text(encoding="utf-8")
        for dependency in re.findall(r"^(ezchain-[a-z0-9-]+)\s*=", text, re.MULTILINE):
            if dependency in frozen:
                out.append(
                    finding(
                        "frozen-dependency",
                        f"rust/{name}",
                        f"shipping crate {name} depends on frozen crate {dependency}",
                        "Either unfreeze the dependency and give it a user, or keep the "
                        "dependent crate frozen too.",
                    )
                )
    # Only the map may name a binary; a crate that grows a second entry point is a
    # deployment the map does not describe.
    binaries = spec["rust_crates"].get("binaries", {})
    for rel in sorted(visible):
        parts = Path(rel).parts
        if not (
            len(parts) == 4
            and parts[0] == "rust"
            and parts[1].startswith("ezchain-")
            and parts[2] == "src"
            and parts[3] == "main.rs"
        ):
            continue
        name = parts[1]
        if name not in binaries:
            out.append(
                finding(
                    "undeclared-binary",
                    rel,
                    f"{name} builds a binary the language map does not list",
                    "Declare it in rust_crates.binaries and say whether an installer ships it.",
                )
            )
    out += check_rust_reachability(spec, crates, shipping, frozen)
    return out


def crate_dependencies(crate: Path) -> set[str]:
    """The workspace crates one crate links, from its own manifest."""
    text = (crate / "Cargo.toml").read_text(encoding="utf-8")
    return set(re.findall(r"^(ezchain-[a-z0-9-]+)\s*=", text, re.MULTILINE))


def reachable_from(entry_points: list[str], crates: dict[str, Path]) -> set[str]:
    """Everything a shipped entry point actually links, transitively."""
    seen: set[str] = set()
    pending = [name for name in entry_points if name in crates]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        pending.extend(crate_dependencies(crates[name]) - seen)
    return seen


def check_rust_reachability(
    spec: dict[str, Any],
    crates: dict[str, Path],
    shipping: set[str],
    frozen: set[str],
) -> list[Finding]:
    """A crate is "shipping" only if a shipped entry point links it.

    This is the check that keeps the map from repeating the mistake it recorded: a crate
    counted as product value while nothing starts it. ``shipping_unwired`` is the honest
    middle state — built, tested, proven, and not yet called by anything; every entry there
    must name the wiring that is missing.
    """
    out = []
    entry_points = spec["rust_crates"].get("entry_points", [])
    unwired = spec["rust_crates"].get("shipping_unwired", {})
    reachable = reachable_from(entry_points, crates)
    for name in sorted(reachable & frozen):
        out.append(
            finding(
                "frozen-but-reachable",
                f"rust/{name}",
                f"a shipped entry point links frozen crate {name}",
                "A frozen crate the product links is not frozen. Either unfreeze it and give "
                "it a purpose, or cut the dependency.",
            )
        )
    for name in sorted(shipping - reachable - set(unwired)):
        out.append(
            finding(
                "shipping-without-a-user",
                f"rust/{name}",
                "this crate is declared shipping, but no shipped entry point links it",
                "Move it to rust_crates.shipping_unwired with the wiring that is missing, or "
                "wire it. Calling an unlinked crate shipping is the drift this check exists "
                "to stop.",
            )
        )
    for name in sorted(set(unwired) & reachable):
        out.append(
            finding(
                "stale-unwired-entry",
                f"rust/{name}",
                "this crate is listed as unwired, and a shipped entry point now links it",
                "Move it to rust_crates.shipping: the wiring landed, so the exception is stale.",
            )
        )
    for name in sorted(set(unwired) - set(crates)):
        out.append(
            finding(
                "missing-crate",
                f"rust/{name}",
                "the map lists an unwired crate that is not in the workspace",
                "Remove the entry.",
            )
        )
    return out


# --- 7. money paths ---------------------------------------------------------------------


def check_money_paths(spec: dict[str, Any]) -> list[Finding]:
    out = []
    rule = spec["forbidden"]["float_money"]
    pattern = re.compile(r"\bfloat\(|\bround\(")
    for rel in rule["paths"]:
        path = ROOT / rel
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                out.append(
                    finding(
                        "float-money",
                        f"{rel}:{number}",
                        "a float conversion in a money path loses the amount",
                        "Use Decimal arithmetic. A display-only conversion belongs in the "
                        "renderer, never in the oracle or the ledger.",
                    )
                )
    return out


# --- 8. renderer boundary ---------------------------------------------------------------

CLIENT_RE = re.compile(r'^\s*["\']use client["\']', re.MULTILINE)


def check_renderer(spec: dict[str, Any], files: list[Path]) -> list[Finding]:
    out = []
    ui_paths = spec["forbidden"]["rust_in_ui"]["paths"]
    py_ui = spec["forbidden"]["python_in_ui"]["paths"]
    sanctioned = {str(Path(entry["path"])) for entry in spec["python_entry_points"]} | {
        "apps/desktop/backend_entry.py"
    }
    for rel in files:
        text_rel = str(rel)
        if any(text_rel.startswith(f"{ui}/") for ui in ui_paths) and rel.suffix in {".rs", ".py"}:
            out.append(
                finding(
                    "rust-in-ui",
                    text_rel,
                    "the UI is TypeScript; no Rust or Python source belongs in it",
                    "Reach the behaviour through the API. A shared type comes from the OpenAPI "
                    "snapshot, not from a copied source file.",
                )
            )
        if (
            any(text_rel.startswith(f"{ui}/") for ui in py_ui)
            and rel.suffix == ".py"
            and text_rel not in sanctioned
        ):
            out.append(
                finding(
                    "python-in-ui",
                    text_rel,
                    "Python in the UI tree is a second implementation waiting to drift",
                    "Move it under apps/api or services/.",
                )
            )
        if rel.suffix in {".ts", ".tsx"} and any(text_rel.startswith(f"{ui}/") for ui in ui_paths):
            text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
            if "EZCHAIN_API_TOKEN" in text and CLIENT_RE.search(text):
                out.append(
                    finding(
                        "token-in-renderer",
                        text_rel,
                        "a client component reads the deployment token, so it ships to the browser",
                        "Read the token in a server component or route handler only (CONTRACT I4).",
                    )
                )
    return out


# --- main -------------------------------------------------------------------------------


def run(spec: dict[str, Any]) -> list[Finding]:
    files = source_file_set()
    paths = [Path(rel) for rel in sorted(files)]
    findings: list[Finding] = []
    findings += check_forbidden_languages(paths, spec)
    findings += check_declared_layers(spec, files)
    findings += check_import_edges(spec, files)
    findings += check_bridge_clients(spec, files)
    findings += check_third_party_seams(spec, files)
    findings += check_rust_crates(spec, files)
    findings += check_money_paths(spec)
    findings += check_renderer(spec, paths)
    return findings


def by_kind(findings: list[Finding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in findings:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    global ROOT, MAP_PATH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    parser.add_argument("--explain", action="store_true", help="print the rules first")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="check this checkout instead of the one containing the script",
    )
    args = parser.parse_args(argv)
    if args.root is not None:
        ROOT = args.root.resolve()
        MAP_PATH = ROOT / "architecture" / "language-map.json"

    spec = read_map()
    findings = run(spec)

    if args.explain:
        print("language map:", MAP_PATH)
        print("python layers:", ", ".join(spec["python_layers"]))
        print("shipping crates:", ", ".join(spec["rust_crates"]["shipping"]))
        print("frozen crates:", ", ".join(spec["rust_crates"]["frozen"]))
        print("unfreeze when:", spec["rust_crates"]["unfreeze_condition"])
        print()

    if args.json:
        print(json.dumps(findings, indent=2, sort_keys=True))
        return 1 if findings else 0

    if not findings:
        count = (
            len(spec["python_layers"])
            + len(spec["rust_crates"]["shipping"])
            + len(spec["rust_crates"]["frozen"])
            + len(spec["rust_crates"].get("shipping_unwired", {}))
        )
        print(f"language-map: ok — {count} declared layers and crates, no undeclared edge")
        return 0

    print(f"language-map: {len(findings)} finding(s)", file=sys.stderr)
    for kind, number in sorted(by_kind(findings).items()):
        print(f"  {kind}: {number}", file=sys.stderr)
    print(file=sys.stderr)
    for item in findings:
        print(f"  [{item['kind']}] {item['where']}", file=sys.stderr)
        print(f"      {item['detail']}", file=sys.stderr)
        print(f"      fix: {item['fix']}", file=sys.stderr)
    print(
        "\nThe rules are architecture/language-map.json and ADR-0050/0052/0053. "
        "A new boundary needs an ADR, not a wider exception list.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
