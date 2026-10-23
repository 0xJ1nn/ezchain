"""The Hermes/OpenClaw packs are installable artifacts, not prose."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERMES = ROOT / "integrations/hermes"
OPENCLAW = ROOT / "integrations/openclaw"


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text()
    assert text.startswith("---\n")
    block = text.split("---\n", 2)[1]
    values: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            values[key.strip()] = value.strip().strip('"')
    return values


def test_openclaw_bundle_declares_the_skill_and_server() -> None:
    manifest = json.loads((OPENCLAW / "plugin.json").read_text())
    assert manifest["$schema"].endswith("/1.0.0/plugin.schema.json")
    assert manifest["name"] == "ezchain"
    mcp = json.loads((OPENCLAW / "mcp.json").read_text())
    server = mcp["mcpServers"]["ezchain"]
    assert server["type"] == "stdio"
    assert server["command"] == "./bin/ezchain-mcp"
    assert (OPENCLAW / "skills/ezchain-trading/SKILL.md").is_file()
    assert (OPENCLAW / "bin/ezchain-mcp").stat().st_mode & 0o111


def test_hermes_skill_and_installer() -> None:
    meta = _frontmatter(HERMES / "SKILL.md")
    assert meta["name"] == "ezchain-trading"
    assert "PAPER" in meta["description"] or "paper" in meta["description"]

    home = Path(os.environ.get("PYTEST_TMPDIR", "/tmp")) / "hermes-pack-test"
    result = subprocess.run(
        ["bash", str(HERMES / "install.sh")],
        env={**os.environ, "HERMES_HOME": str(home)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert (home / "skills/ezchain-trading/SKILL.md").is_file()
    assert "hermes mcp add ezchain" in result.stdout


def test_scripts_parse_and_packs_contain_no_minted_tokens() -> None:
    for script in (HERMES / "install.sh", OPENCLAW / "bin/ezchain-mcp"):
        subprocess.run(["bash", "-n", str(script)], check=True)
    token = re.compile(r"ezk_[A-Za-z0-9_-]{30,}")
    for path in [*HERMES.rglob("*"), *OPENCLAW.rglob("*")]:
        if path.is_file() and path.suffix in {".md", ".json", ".yaml", ".sh"}:
            assert not token.search(path.read_text()), path
