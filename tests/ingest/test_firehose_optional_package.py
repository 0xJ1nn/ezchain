"""The live source is an extra: importing the package and retention never need it.

``pumpfun-firehose`` is optional. Retention, replay and the adapter's normalisation run on
a machine that never installed it, so ``import ezchain_ingest`` and the retention CLI are
exercised in a child interpreter where ``pumpfire`` cannot be imported at all. The child
blocks the name with a meta-path finder instead of uninstalling anything, so the check is
exact and leaves the environment alone.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INGEST_PYPROJECT = ROOT / "services" / "ingest" / "pyproject.toml"

BLOCK_PUMPFIRE = """
import sys

class _NoPumpfire:
    def find_spec(self, name, path=None, target=None):
        if name == "pumpfire" or name.startswith("pumpfire."):
            raise ModuleNotFoundError(f"No module named {name!r}", name="pumpfire")
        return None

sys.meta_path.insert(0, _NoPumpfire())
"""


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", BLOCK_PUMPFIRE + script],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )


def test_the_package_imports_without_the_optional_source_package() -> None:
    result = _run("import ezchain_ingest\nprint(ezchain_ingest.prune.__name__)")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "prune"


def test_the_retention_cli_runs_without_the_optional_source_package() -> None:
    result = _run(
        "import runpy, sys\n"
        "sys.argv = ['ezchain_ingest', '--help']\n"
        "runpy.run_module('ezchain_ingest', run_name='__main__')\n"
    )

    assert result.returncode == 0, result.stderr
    assert "--apply" in result.stdout


def test_using_the_live_source_without_the_package_names_the_extra() -> None:
    result = _run(
        "from ezchain_ingest import FirehoseSource\n"
        "try:\n"
        "    FirehoseSource()\n"
        "except ModuleNotFoundError as exc:\n"
        "    print(exc)\n"
    )

    assert result.returncode == 0, result.stderr
    assert "ezchain-ingest[firehose]" in result.stdout


def test_the_source_package_is_an_optional_extra_pinned_to_the_release_tag() -> None:
    project = tomllib.loads(INGEST_PYPROJECT.read_text(encoding="utf-8"))["project"]

    assert not any("pumpfun-firehose" in requirement for requirement in project["dependencies"])
    (requirement,) = project["optional-dependencies"]["firehose"]
    assert requirement == (
        "pumpfun-firehose @ git+https://github.com/0xJ1nn/pumpfun-firehose.git@v0.5.0"
    )


@pytest.mark.parametrize("name", ["requirements.txt", "requirements-runtime.txt"])
def test_no_requirement_file_installs_the_optional_source(name: str) -> None:
    lines = [
        line.strip()
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert not any("pumpfun-firehose" in line for line in lines)
