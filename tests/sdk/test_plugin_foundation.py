"""Manifest v2, capability registry and discovery — data only, no plugin import."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from ezchain_sdk.plugin import (
    CAPABILITIES,
    KINDS,
    PluginManifestError,
    can_request,
    effective_capabilities,
    host_scope,
    host_surface,
    parse_legacy_manifest,
    parse_plugin_manifest,
)

VALID: dict[str, object] = {
    "schema": "ezchain.plugin/2",
    "id": "com.example.momentum",
    "name": "Momentum",
    "version": "1.2.0",
    "kind": "strategy",
    "capabilities": ["read_market", "read_account", "propose_order"],
    "required_scopes": ["read", "trade"],
    "compatibility": {"manifest": "2", "host": ">=0.1.0,<0.2.0"},
    "config_schema": {
        "type": "object",
        "properties": {"lookback": {"type": "integer", "default": 20}},
        "additionalProperties": False,
    },
    "license": "MIT",
    "author": "Example Author",
    "source_hash": "sha256:" + "a" * 64,
    "description": "Momentum on closed bars.",
    "entrypoint": "example.strategy:Momentum",
}


def manifest(**changes: object) -> dict[str, object]:
    data = copy.deepcopy(VALID)
    data.update(changes)
    return data


def write_plugin(root: Path, data: object, *, name: str = "ezchain.plugin.json") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_valid_manifest_parses_to_a_typed_record() -> None:
    record = parse_plugin_manifest(VALID)
    assert record.id == "com.example.momentum"
    assert record.kind == "strategy"
    assert record.capabilities == ("read_market", "read_account", "propose_order")
    assert record.required_scopes == ("read", "trade")
    assert record.compatibility.host == ">=0.1.0,<0.2.0"
    assert record.source_hash == "sha256:" + "a" * 64
    assert record.to_dict()["schema"] == "ezchain.plugin/2"


def test_registry_enumerates_exactly_the_declared_kinds_and_capabilities() -> None:
    assert KINDS == (
        "agent_tool",
        "automation_template",
        "data_source",
        "evaluator",
        "report_exporter",
        "strategy",
        "ui_widget",
        "venue",
    )
    assert CAPABILITIES == (
        "propose_order",
        "read_account",
        "read_ledger",
        "read_market",
        "render_widget",
        "write_config",
    )


def test_registry_answers_kind_capability_and_host_surface() -> None:
    assert can_request("strategy", "propose_order") is True
    assert can_request("ui_widget", "propose_order") is False
    assert "ctx.candles_for" in host_surface("read_market")


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "name",
        "version",
        "kind",
        "capabilities",
        "required_scopes",
        "compatibility",
        "config_schema",
        "license",
        "author",
        "source_hash",
    ],
)
def test_each_required_field_is_enforced(field: str) -> None:
    data = manifest()
    del data[field]
    with pytest.raises(PluginManifestError) as exc:
        parse_plugin_manifest(data)
    assert exc.value.code == "PLUGIN_MANIFEST_MISSING_FIELD"
    assert exc.value.field == field


def test_schema_field_is_required() -> None:
    data = manifest()
    del data["schema"]
    with pytest.raises(PluginManifestError) as exc:
        parse_plugin_manifest(data)
    assert exc.value.code == "PLUGIN_MANIFEST_SCHEMA_UNSUPPORTED"


def test_unknown_top_level_key_is_refused_but_x_extension_is_allowed() -> None:
    with pytest.raises(PluginManifestError) as exc:
        parse_plugin_manifest(manifest(capabilites=["read_market"]))
    assert exc.value.code == "PLUGIN_MANIFEST_UNKNOWN_KEY"
    assert "capabilites" in exc.value.message
    parse_plugin_manifest(manifest(**{"x-review-note": "seen"}))


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"kind": "kernel_module"}, "PLUGIN_KIND_UNKNOWN"),
        ({"capabilities": ["read_vault"]}, "PLUGIN_CAPABILITY_UNKNOWN"),
        ({"capabilities": ["read_market", "read_market"]}, "PLUGIN_MANIFEST_INVALID"),
        ({"required_scopes": ["admin"]}, "PLUGIN_SCOPE_UNKNOWN"),
        ({"id": "Bad ID"}, "PLUGIN_MANIFEST_INVALID"),
        ({"name": "   "}, "PLUGIN_MANIFEST_INVALID"),
        ({"license": ""}, "PLUGIN_MANIFEST_INVALID"),
        ({"author": 42}, "PLUGIN_MANIFEST_INVALID"),
        ({"version": "1.2"}, "PLUGIN_MANIFEST_INVALID"),
        ({"version": "v1.2.0"}, "PLUGIN_MANIFEST_INVALID"),
        ({"sdk": "not a range"}, "PLUGIN_MANIFEST_INVALID"),
        ({"sdk": ">=2,<3"}, "PLUGIN_SDK_INCOMPATIBLE"),
        ({"capabilities": "read_market"}, "PLUGIN_MANIFEST_INVALID"),
        ({"config_schema": []}, "PLUGIN_MANIFEST_INVALID"),
        (
            {"config_schema": {"type": "object", "patternProperties": {}}},
            "PLUGIN_SCHEMA_UNSUPPORTED",
        ),
    ],
)
def test_invalid_field_values_are_refused(change: dict[str, object], code: str) -> None:
    with pytest.raises(PluginManifestError) as exc:
        parse_plugin_manifest(manifest(**change))
    assert exc.value.code == code


def test_manifest_must_be_an_object() -> None:
    with pytest.raises(PluginManifestError) as exc:
        parse_plugin_manifest(["not", "an", "object"])
    assert exc.value.code == "PLUGIN_MANIFEST_INVALID"


@pytest.mark.parametrize(
    "compat",
    [
        {"host": ">=0.1.0,<0.2.0"},
        {"manifest": "2", "host": "??"},
        {"manifest": "3", "host": ">=0.1.0"},
        {"manifest": "2", "host": ">=0.1.0", "tier": "A"},
    ],
)
def test_compatibility_block_is_strict(compat: object) -> None:
    with pytest.raises(PluginManifestError) as exc:
        parse_plugin_manifest(manifest(compatibility=compat))
    assert exc.value.code in {
        "PLUGIN_MANIFEST_MISSING_FIELD",
        "PLUGIN_MANIFEST_INVALID",
        "PLUGIN_SCHEMA_UNSUPPORTED",
    }


def test_unknown_schema_version_is_refused() -> None:
    with pytest.raises(PluginManifestError) as exc:
        parse_plugin_manifest(manifest(schema="ezchain.plugin/3"))
    assert exc.value.code == "PLUGIN_MANIFEST_SCHEMA_UNSUPPORTED"


def test_host_version_outside_the_declared_range_is_refused() -> None:
    with pytest.raises(PluginManifestError) as exc:
        parse_plugin_manifest(manifest(), host_version="0.2.0")
    assert exc.value.code == "PLUGIN_HOST_INCOMPATIBLE"


def test_host_version_inside_the_declared_range_is_accepted() -> None:
    record = parse_plugin_manifest(manifest(), host_version="0.1.4")
    assert record.id == "com.example.momentum"


@pytest.mark.parametrize(
    "value",
    [
        "a" * 64,
        "sha256:" + "A" * 64,
        "sha256:" + "g" * 64,
        "sha256:" + "a" * 63,
        "sha1:" + "a" * 40,
        "sha256:" + "a" * 64 + "extra",
    ],
)
def test_bad_source_hash_is_refused(value: str) -> None:
    with pytest.raises(PluginManifestError) as exc:
        parse_plugin_manifest(manifest(source_hash=value))
    assert exc.value.code == "PLUGIN_SOURCE_HASH_INVALID"


@pytest.mark.parametrize(
    ("kind", "capability"),
    [
        ("ui_widget", "propose_order"),
        ("data_source", "write_config"),
        ("strategy", "read_ledger"),
        ("venue", "write_config"),
        ("automation_template", "propose_order"),
        ("agent_tool", "propose_order"),
    ],
)
def test_capability_escalation_by_kind_is_refused(kind: str, capability: str) -> None:
    data = manifest(kind=kind, capabilities=[capability])
    with pytest.raises(PluginManifestError) as exc:
        parse_plugin_manifest(data)
    assert exc.value.code == "PLUGIN_CAPABILITY_NOT_ALLOWED"


def test_every_kind_has_a_non_empty_capability_allowlist() -> None:
    for kind in KINDS:
        assert can_request(kind, "read_market") or can_request(kind, "render_widget")


def test_effective_capabilities_is_a_data_only_intersection() -> None:
    assert effective_capabilities("strategy", ["propose_order", "write_config"]) == (
        "propose_order",
    )
    assert effective_capabilities("unknown", ["read_market"]) == ()


def test_host_surface_and_scope_are_descriptive_data() -> None:
    assert "intent" in host_surface("propose_order").lower()
    assert host_scope("write_config") == "config"
    with pytest.raises(ValueError):
        host_surface("read_vault")


def test_legacy_v1_manifest_maps_to_a_strategy_record() -> None:
    legacy = {
        "id": "com.example.momentum",
        "name": "Momentum",
        "version": "1.2.0",
        "sdk": ">=1,<2",
        "entrypoint": "my_strategy.strategy:Momentum",
        "permissions": ["read_market", "read_account", "submit_intent"],
        "params_schema": {"type": "object", "properties": {}},
        "modes": ["paper"],
    }
    record = parse_legacy_manifest(legacy)
    assert record.schema == "ezchain.plugin/1"
    assert record.kind == "strategy"
    assert record.capabilities == ("propose_order", "read_account", "read_market")
    assert record.required_scopes == ()
    assert record.source_hash is None
    assert record.compatibility.manifest == "1"
    assert record.compatibility.host == "*"
    assert record.entrypoint == "my_strategy.strategy:Momentum"


def test_discovery_returns_a_typed_record_and_computes_the_manifest_hash(
    tmp_path: Path,
) -> None:
    from ezchain_sdk.plugin import discover_plugins

    path = write_plugin(tmp_path / "momentum", manifest())
    result = discover_plugins(tmp_path)
    assert result.issues == ()
    assert len(result.records) == 1
    record = result.records[0]
    assert record.manifest.id == "com.example.momentum"
    assert record.root == path.parent
    assert record.manifest_path == path
    assert record.manifest_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result.to_dict()["records"][0]["id"] == "com.example.momentum"


def test_discovery_sorts_records_and_accepts_a_string_path(tmp_path: Path) -> None:
    from ezchain_sdk.plugin import discover_plugins

    write_plugin(tmp_path / "zeta", manifest(id="com.example.zeta", name="Zeta"))
    write_plugin(tmp_path / "alpha", manifest(id="com.example.alpha", name="Alpha"))
    result = discover_plugins(str(tmp_path))
    assert [record.manifest.id for record in result.records] == [
        "com.example.alpha",
        "com.example.zeta",
    ]


def test_invalid_manifest_yields_a_structured_issue_without_hiding_valid_plugins(
    tmp_path: Path,
) -> None:
    from ezchain_sdk.plugin import discover_plugins

    write_plugin(tmp_path / "good", manifest())
    write_plugin(tmp_path / "bad", manifest(kind="telepathy"))
    result = discover_plugins(tmp_path)
    assert [record.manifest.id for record in result.records] == ["com.example.momentum"]
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.code == "PLUGIN_KIND_UNKNOWN"
    assert issue.field == "kind"
    assert "telepathy" in issue.message
    assert str(tmp_path / "bad" / "ezchain.plugin.json") == issue.path
    assert result.ok is False


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"capabilities": ["read_vault"]}, "PLUGIN_CAPABILITY_UNKNOWN"),
        ({"kind": "ui_widget", "capabilities": ["propose_order"]}, "PLUGIN_CAPABILITY_NOT_ALLOWED"),
        ({"source_hash": "sha256:deadbeef"}, "PLUGIN_SOURCE_HASH_INVALID"),
        ({"version": "1.2"}, "PLUGIN_MANIFEST_INVALID"),
    ],
)
def test_discovery_reports_invalid_manifests_with_stable_codes(
    tmp_path: Path, change: dict[str, object], code: str
) -> None:
    from ezchain_sdk.plugin import discover_plugins

    write_plugin(tmp_path / "plugin", manifest(**change))
    result = discover_plugins(tmp_path)
    assert result.records == ()
    assert len(result.issues) == 1
    assert result.issues[0].code == code


def test_duplicate_ids_keep_the_first_discovery_and_issue_a_warning(
    tmp_path: Path,
) -> None:
    from ezchain_sdk.plugin import discover_plugins

    first = write_plugin(tmp_path / "a_first", manifest())
    write_plugin(tmp_path / "b_second", manifest(name="A copy"))
    result = discover_plugins(tmp_path)
    assert len(result.records) == 1
    assert result.records[0].manifest_path == first
    assert [issue.code for issue in result.issues] == ["PLUGIN_DUPLICATE_ID"]


def test_missing_path_is_a_structured_issue(tmp_path: Path) -> None:
    from ezchain_sdk.plugin import discover_plugins

    result = discover_plugins(tmp_path / "absent")
    assert result.records == ()
    assert [issue.code for issue in result.issues] == ["PLUGIN_PATH_MISSING"]


def test_directory_without_a_manifest_is_a_structured_issue(tmp_path: Path) -> None:
    from ezchain_sdk.plugin import discover_plugins

    (tmp_path / "empty").mkdir()
    result = discover_plugins(tmp_path)
    assert [issue.code for issue in result.issues] == ["PLUGIN_MANIFEST_MISSING"]


def test_malformed_json_is_a_structured_issue(tmp_path: Path) -> None:
    from ezchain_sdk.plugin import discover_plugins

    plugin = tmp_path / "broken" / "ezchain.plugin.json"
    plugin.parent.mkdir()
    plugin.write_text("{not json", encoding="utf-8")
    result = discover_plugins(tmp_path)
    assert [issue.code for issue in result.issues] == ["PLUGIN_MANIFEST_INVALID"]


def test_oversized_manifest_is_refused_before_parsing(tmp_path: Path) -> None:
    from ezchain_sdk.plugin import discover_plugins

    plugin = tmp_path / "huge" / "ezchain.plugin.json"
    plugin.parent.mkdir()
    plugin.write_bytes(b'{"x": "' + b"a" * (300 * 1024) + b'"}')
    result = discover_plugins(tmp_path)
    assert [issue.code for issue in result.issues] == ["PLUGIN_MANIFEST_TOO_LARGE"]


def test_nested_plugin_directories_are_not_discovered(tmp_path: Path) -> None:
    from ezchain_sdk.plugin import discover_plugins

    write_plugin(tmp_path / "outer" / "inner", manifest())
    result = discover_plugins(tmp_path)
    assert result.records == ()
    assert result.issues == ()


def test_discovery_never_imports_executes_subprocesses_or_opens_sockets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ezchain_sdk.plugin import discover_plugins

    plugin = tmp_path / "hostile"
    write_plugin(plugin, manifest(entrypoint="hostile_plugin_module:Entry"))
    marker = plugin / "imported.marker"
    (plugin / "hostile_plugin_module.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported')\n"
        "raise RuntimeError('plugin code must never run during discovery')\n",
        encoding="utf-8",
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("discovery tried to execute plugin code")

    monkeypatch.setattr(importlib, "import_module", forbidden, raising=True)
    monkeypatch.setattr(subprocess, "Popen", forbidden, raising=True)
    monkeypatch.setattr(socket, "socket", forbidden, raising=True)

    result = discover_plugins(tmp_path)
    assert result.issues == ()
    assert len(result.records) == 1
    assert not marker.exists()
    assert "hostile_plugin_module" not in sys.modules


def test_legacy_manifest_is_discovered_as_a_strategy(tmp_path: Path) -> None:
    from ezchain_sdk.plugin import discover_plugins

    write_plugin(
        tmp_path / "legacy",
        {
            "id": "com.example.legacy",
            "name": "Legacy",
            "version": "1.0.0",
            "sdk": ">=1,<2",
            "entrypoint": "legacy.strategy:Legacy",
            "permissions": ["read_market", "submit_intent"],
            "params_schema": {"type": "object", "properties": {}},
            "modes": ["paper"],
        },
    )
    result = discover_plugins(tmp_path)
    assert result.issues == ()
    assert result.records[0].manifest.schema == "ezchain.plugin/1"
    assert result.records[0].manifest.kind == "strategy"


def test_host_version_outside_range_is_reported_as_an_issue(tmp_path: Path) -> None:
    from ezchain_sdk.plugin import discover_plugins

    write_plugin(tmp_path / "plugin", manifest())
    result = discover_plugins(tmp_path, host_version="0.2.0")
    assert result.records == ()
    assert [issue.code for issue in result.issues] == ["PLUGIN_HOST_INCOMPATIBLE"]


def test_manifest_file_can_be_passed_directly(tmp_path: Path) -> None:
    from ezchain_sdk.plugin import discover_plugins

    path = write_plugin(tmp_path / "plugin", manifest())
    result = discover_plugins(path)
    assert result.issues == ()
    assert result.records[0].manifest_path == path
    assert result.records[0].root == path.parent


def test_cli_list_prints_records_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from ezchain_sdk.plugin.cli import main

    write_plugin(tmp_path / "plugin", manifest())
    assert main(["list", "--path", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "OK com.example.momentum 1.2.0 strategy" in output


def test_cli_validate_reports_issues_and_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from ezchain_sdk.plugin.cli import main

    write_plugin(tmp_path / "bad", manifest(kind="ui_widget", capabilities=["propose_order"]))
    assert main(["validate", "--path", str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert "ISSUE PLUGIN_CAPABILITY_NOT_ALLOWED" in output


def test_cli_json_output_is_structured(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from ezchain_sdk.plugin.cli import main

    write_plugin(tmp_path / "plugin", manifest())
    assert main(["list", "--path", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["records"][0]["id"] == "com.example.momentum"


def test_cli_host_version_outside_range_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from ezchain_sdk.plugin.cli import main

    write_plugin(tmp_path / "plugin", manifest())
    assert main(["validate", "--path", str(tmp_path), "--host-version", "0.2.0"]) == 1
    assert "PLUGIN_HOST_INCOMPATIBLE" in capsys.readouterr().out


def test_cli_missing_path_argument_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    from ezchain_sdk.plugin.cli import main

    assert main(["list"]) == 2
    capsys.readouterr()


def test_cli_unknown_command_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    from ezchain_sdk.plugin.cli import main

    assert main(["execute"]) == 2
    capsys.readouterr()


def test_cli_discovery_does_not_run_plugin_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ezchain_sdk.plugin.cli import main

    plugin = tmp_path / "hostile"
    write_plugin(plugin, manifest())
    marker = plugin / "imported.marker"
    (plugin / "hostile_cli_module.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n"
        "raise RuntimeError('must not import')\n",
        encoding="utf-8",
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("CLI tried to execute plugin code")

    monkeypatch.setattr(importlib, "import_module", forbidden, raising=True)
    monkeypatch.setattr(subprocess, "Popen", forbidden, raising=True)
    assert main(["validate", "--path", str(tmp_path)]) == 0
    assert not marker.exists()
