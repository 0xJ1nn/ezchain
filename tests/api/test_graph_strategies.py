"""The ``graph`` strategy kind on the existing create/patch surface."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ezchain_api.models import StrategyRow
from ezchain_api.routers import strategies

CHAIN = "solana"
TOKEN = "So11111111111111111111111111111111111111112"


def node(node_id: str, node_type: str, **params: Any) -> dict[str, Any]:
    return {"id": node_id, "type": node_type, "params": params, "ui": {}}


def edge(source: str, source_port: str, target: str, target_port: str) -> dict[str, Any]:
    return {
        "from": {"node": source, "port": source_port},
        "to": {"node": target, "port": target_port},
    }


def valid_graph() -> dict[str, Any]:
    return {
        "schema": "ezchain.graph/1",
        "nodes": [
            node("tick", "event.tick"),
            node("buy", "act.buy", amount_usd="10"),
            node("sell", "act.sell", pct_position="100"),
        ],
        "edges": [
            edge("tick", "out", "buy", "in"),
            edge("buy", "out", "sell", "in"),
        ],
        "comments": [],
    }


def invalid_graph() -> dict[str, Any]:
    return {
        "schema": "ezchain.graph/1",
        "nodes": [node("tick", "event.tick")],
        "edges": [],
        "comments": [],
    }


def create_body(graph: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "EMA cross",
        "chain": CHAIN,
        "token_address": TOKEN,
        "token_symbol": "TEST",
        "kind": "graph",
        "params": {
            "graph": graph,
            "interval_minutes": 15,
            "max_position_usd": "250",
        },
    }
    body.update(overrides)
    return body


def test_create_graph_strategy(client_for: Callable[..., TestClient], session: Session) -> None:
    client: TestClient = client_for(strategies.router)
    response = client.post("/api/v1/strategies", json=create_body(valid_graph()))

    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "graph"
    assert body["params"]["graph"]["schema"] == "ezchain.graph/1"
    assert body["params"]["interval_minutes"] == 15
    assert session.get(StrategyRow, body["id"]) is not None


def test_create_rejects_an_invalid_graph_with_the_report(
    client_for: Callable[..., TestClient],
) -> None:
    client: TestClient = client_for(strategies.router)
    response = client.post("/api/v1/strategies", json=create_body(invalid_graph()))

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "STRATEGY_GRAPH_INVALID"
    assert detail["report"]["ok"] is False
    assert {issue["code"] for issue in detail["report"]["errors"]} >= {"NO_ACTION"}


def test_patch_revalidates_graph_params(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    client: TestClient = client_for(strategies.router)
    created = client.post("/api/v1/strategies", json=create_body(valid_graph()))
    strategy_id = created.json()["id"]

    rejected = client.patch(
        f"/api/v1/strategies/{strategy_id}",
        json={"params": {"graph": invalid_graph()}},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "STRATEGY_GRAPH_INVALID"

    accepted = client.patch(
        f"/api/v1/strategies/{strategy_id}",
        json={
            "params": {
                "graph": valid_graph(),
                "interval_minutes": 30,
                "max_position_usd": "500",
            }
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["params"]["interval_minutes"] == 30
