"""Schema-driven fuzzing of the order and control APIs.

Schemathesis is neither installed nor pinned in this repository, so the generator reads the
live OpenAPI request schemas and drives them with seeded random values. Every response is
checked for the one invariant that must never break: no server error.
"""

from __future__ import annotations

import random
from decimal import Decimal
from functools import lru_cache
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from ezchain_adapters import QuoteRouter
from ezchain_api.config import Settings, get_settings
from ezchain_api.deps import get_dexscreener, get_quote_router, get_session
from ezchain_api.errors import request_validation_error
from ezchain_api.main import create_app
from ezchain_api.routers import paper as paper_router
from ezchain_api.routers import system as system_router
from tests.paper.conftest import TOKEN, make_snapshot

TARGETS = ("/api/v1/paper/orders", "/api/v1/system/kill-switch")
STRINGS = ("", "x", "x" * 300, "✓", "0x" + "0" * 40, "2026-01-01T00:00:00Z")


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    return create_app().openapi()


def _body_schema(path: str) -> dict[str, Any]:
    operation = _schema()["paths"][path]["post"]
    reference = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    name = reference.rsplit("/", 1)[-1]
    return _schema()["components"]["schemas"][name]


def _value_for(prop: dict[str, Any], name: str, rng: random.Random) -> Any:
    if "enum" in prop:
        return rng.choice(prop["enum"])
    if "anyOf" in prop:
        choices = [item for item in prop["anyOf"] if item.get("type") != "null"]
        return _value_for(rng.choice(choices), name, rng)
    kind = prop.get("type")
    if kind == "boolean":
        return rng.choice([True, False])
    if kind in ("number", "integer"):
        return rng.choice([0, -1, 1, 25, 1000, 10**30, -0.5, 1e-9, "not-a-number"])
    if kind == "string":
        if name == "idempotency_key":
            return rng.choice([f"fuzz-{rng.randint(0, 10**9)}", "short", "x" * 200, "✓-key"])
        if name == "chain":
            return rng.choice(["blast", "solana", "base", "", "x" * 100])
        if name == "token_address":
            return rng.choice([TOKEN, "", "abc", "0x" + "z" * 200])
        return rng.choice(STRINGS)
    return None


def _payload(path: str, rng: random.Random) -> dict[str, Any]:
    schema = _body_schema(path)
    properties: dict[str, Any] = schema["properties"]
    required: list[str] = schema.get("required", [])
    payload: dict[str, Any] = {}
    for name in required:
        payload[name] = _value_for(properties[name], name, rng)
    for name in properties:
        if name not in payload and rng.random() < 0.4:
            payload[name] = _value_for(properties[name], name, rng)
    mutation = rng.choice(["none", "none", "missing", "null", "extra", "oversize"])
    if mutation == "missing" and required:
        payload.pop(rng.choice(required))
    elif mutation == "null" and properties:
        payload[rng.choice(list(properties))] = None
    elif mutation == "extra":
        payload["unexpected_field"] = "not in the schema"
    elif mutation == "oversize" and properties:
        payload[rng.choice(list(properties))] = "x" * 10_000
    return payload


def _client(session, settings: Settings, dex) -> TestClient:
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, cast(Any, request_validation_error))
    app.include_router(paper_router.router)
    app.include_router(system_router.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_dexscreener] = lambda: dex
    app.dependency_overrides[get_quote_router] = lambda: QuoteRouter(
        providers=[],
        dexscreener=dex,  # type: ignore[arg-type]
    )
    return TestClient(app, raise_server_exceptions=False)


def test_both_mutation_surfaces_are_in_the_openapi_schema() -> None:
    for path in TARGETS:
        schema = _body_schema(path)
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["required"]


@pytest.mark.parametrize("seed", range(6))
def test_schema_driven_fuzz_never_returns_a_server_error(session, settings, dex, seed: int) -> None:
    client = _client(session, settings, dex)
    dex.set(make_snapshot(price=Decimal("2000")))
    rng = random.Random(seed)
    seen: set[int] = set()

    for index in range(24):
        path = TARGETS[index % len(TARGETS)]
        payload = _payload(path, rng)
        response = client.post(path, json=payload)
        seen.add(response.status_code)
        assert response.status_code < 500, (
            f"seed {seed} case {index} on {path} with {payload!r} returned "
            f"{response.status_code}: {response.text[:300]}"
        )
        if response.status_code >= 400:
            assert isinstance(response.json(), (dict, list))

    assert any(code < 400 for code in seen), f"seed {seed} never reached an accepted request"
    assert any(400 <= code < 500 for code in seen), f"seed {seed} never reached a rejection"
