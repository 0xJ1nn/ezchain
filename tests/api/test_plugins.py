"""Plugin catalogue and plugin-strategy CRUD through the API edge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ezchain_api.deps import get_plugin_registry
from ezchain_api.routers import plugins, strategies
from ezchain_engine import PluginRegistry

MANIFEST = {
    "id": "com.example.api",
    "name": "API test plugin",
    "version": "2.1.0",
    "sdk": ">=1,<2",
    "entrypoint": "api_plugin:Plugin",
    "permissions": ["read_market", "submit_intent"],
    "chains": ["solana"],
    "params_schema": {
        "type": "object",
        "properties": {
            "entry_pct": {"type": "number", "minimum": 0, "default": 1},
            "lookback": {"type": "integer", "minimum": 2, "default": 20},
        },
        "additionalProperties": False,
    },
}


@pytest.fixture
def registry(tmp_path: Path) -> PluginRegistry:
    root = tmp_path / "api_plugin"
    root.mkdir()
    (root / "ezchain.plugin.json").write_text(json.dumps(MANIFEST))
    (root / "api_plugin.py").write_text("from ezchain_sdk import Strategy\n\nclass Plugin(Strategy):\n    pass\n")
    return PluginRegistry(paths=[tmp_path], include_entry_points=False)


def _strategy(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": "plugin momentum",
        "chain": "solana",
        "token_address": "So11111111111111111111111111111111111111112",
        "token_symbol": "SOL",
        "kind": "plugin",
        "plugin_id": "com.example.api",
        "params": {
            "interval_minutes": 60,
            "max_position_usd": 500,
            "config": {"entry_pct": 2},
        },
    }
    body.update(overrides)
    return body


def test_catalogue_and_parameter_validation(
    client_for, registry: PluginRegistry
) -> None:
    client: TestClient = client_for(
        plugins.router,
        overrides={get_plugin_registry: lambda: registry},
    )
    listed = client.get("/api/v1/plugins")
    assert listed.status_code == 200
    assert listed.json()["plugins"][0]["id"] == "com.example.api"

    validated = client.post(
        "/api/v1/plugins/com.example.api/validate",
        json={"config": {"lookback": 30}},
    )
    assert validated.status_code == 200
    assert validated.json()["config"] == {"lookback": 30, "entry_pct": 1}

    invalid = client.post(
        "/api/v1/plugins/com.example.api/validate",
        json={"config": {"lookback": 1}},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "PLUGIN_PARAMS_INVALID"

    missing = client.get("/api/v1/plugins/com.example.missing")
    assert missing.status_code == 404


def test_create_plugin_strategy_pins_the_manifest_version(
    client_for, registry: PluginRegistry, session: Session
) -> None:
    client: TestClient = client_for(
        strategies.router,
        overrides={get_plugin_registry: lambda: registry},
    )
    created = client.post("/api/v1/strategies", json=_strategy())
    assert created.status_code == 201
    body = created.json()
    assert body["plugin_id"] == "com.example.api"
    assert body["plugin_version"] == "2.1.0"
    assert body["params"]["config"] == {"entry_pct": 2}

    readiness = client.get(f"/api/v1/strategies/{body['id']}/live-readiness")
    assert readiness.status_code == 200
    gate = readiness.json()
    assert gate["eligible"] is False
    assert gate["mode"] == "paper"
    assert gate["live_available"] is False
    assert {check["id"] for check in gate["checks"]} >= {"paper_days", "intents", "fills"}


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        (_strategy(params={"interval_minutes": 60, "config": {"lookback": 1}}), 422, "PLUGIN_PARAMS_INVALID"),
        (_strategy(plugin_id="com.example.missing"), 404, "PLUGIN_NOT_FOUND"),
        (_strategy(chain="base"), 422, "PLUGIN_CHAIN_UNSUPPORTED"),
        (_strategy(plugin_version="1.0.0"), 422, "PLUGIN_VERSION_MISMATCH"),
        (
            _strategy(params={"interval_minutes": 60, "config": {"unknown": 1}}),
            422,
            "PLUGIN_PARAMS_INVALID",
        ),
    ],
)
def test_create_refuses_an_invalid_plugin_strategy(
    client_for,
    registry: PluginRegistry,
    payload: dict[str, object],
    status: int,
    code: str,
) -> None:
    client: TestClient = client_for(
        strategies.router,
        overrides={get_plugin_registry: lambda: registry},
    )
    response = client.post("/api/v1/strategies", json=payload)
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
