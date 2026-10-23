"""Manifest validation is the install-time trust boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ezchain_sdk import (
    ManifestError,
    PluginManifest,
    check_compatible,
    load_manifest,
    parse_manifest,
)

BASE = {
    "id": "com.example.momentum",
    "name": "Momentum",
    "version": "1.2.0",
    "sdk": ">=1,<2",
    "entrypoint": "my_strategy.strategy:Momentum",
    "description": "Enters on momentum.",
    "permissions": ["read_market", "submit_intent"],
    "params_schema": {
        "type": "object",
        "properties": {"lookback": {"type": "integer", "minimum": 5, "default": 20}},
        "required": ["lookback"],
    },
    "timeframes": ["1h"],
    "primary_timeframe": "1h",
    "modes": ["paper"],
}


def test_manifest_round_trips_and_is_paper_only() -> None:
    manifest = parse_manifest(BASE)
    assert isinstance(manifest, PluginManifest)
    assert manifest.id == "com.example.momentum"
    assert manifest.permissions == frozenset({"read_market", "submit_intent"})
    assert manifest.timeframes == ("1h",)
    assert manifest.is_paper_only
    assert manifest.to_dict()["params_schema"] == BASE["params_schema"]


def test_primary_timeframe_is_added_to_the_declared_series() -> None:
    manifest = parse_manifest({**BASE, "timeframes": ["5m"], "primary_timeframe": "1h"})
    assert manifest.timeframes == ("5m", "1h")


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"id": "Bad Id"}, "PLUGIN_MANIFEST_INVALID"),
        ({"version": "1.2"}, "PLUGIN_MANIFEST_INVALID"),
        ({"sdk": "not a range"}, "PLUGIN_MANIFEST_INVALID"),
        ({"entrypoint": "module.Class"}, "PLUGIN_MANIFEST_INVALID"),
        ({"permissions": ["read_wallet"]}, "PLUGIN_MANIFEST_INVALID"),
        ({"modes": ["live"]}, "PLUGIN_MANIFEST_INVALID"),
        ({"timeframes": ["2m"]}, "PLUGIN_MANIFEST_INVALID"),
        ({"context_bars": 1}, "PLUGIN_MANIFEST_INVALID"),
        ({"typo": True}, "PLUGIN_MANIFEST_UNKNOWN_KEY"),
    ],
)
def test_manifest_refuses_unsafe_or_unknown_fields(change: dict[str, object], code: str) -> None:
    with pytest.raises(ManifestError) as caught:
        parse_manifest({**BASE, **change})
    assert caught.value.code == code


def test_extension_keys_are_allowed() -> None:
    manifest = parse_manifest({**BASE, "x-vendor": {"theme": "dark"}})
    assert manifest.id == "com.example.momentum"


def test_sdk_compatibility_is_a_range_check() -> None:
    check_compatible(parse_manifest(BASE), "1.0.0")
    with pytest.raises(ManifestError) as caught:
        check_compatible(parse_manifest({**BASE, "sdk": ">=2,<3"}), "1.0.0")
    assert caught.value.code == "PLUGIN_SDK_INCOMPATIBLE"


def test_load_manifest_reads_json_and_rejects_oversize(tmp_path: Path) -> None:
    path = tmp_path / "ezchain.plugin.json"
    path.write_text(json.dumps(BASE))
    assert load_manifest(path).name == "Momentum"

    path.write_text("{" + " " * (256 * 1024) + "}")
    with pytest.raises(ManifestError) as caught:
        load_manifest(path)
    assert caught.value.code == "PLUGIN_MANIFEST_TOO_LARGE"
