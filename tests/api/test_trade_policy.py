"""The persisted trade-policy settings route: defaults, validation, auth and preview."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_api.auth import BearerAuthMiddleware
from ezchain_api.config import Settings, get_settings
from ezchain_api.deps import get_session
from ezchain_api.models import AppSettingRow, LedgerEntryRow
from ezchain_api.routers import settings as settings_router
from ezchain_api.services import paper
from ezchain_api.services.trade_policy import quantize_resolution
from ezchain_core import TradeSizeLimits, TradeSizeResolution


def _limits(
    min_amount: str = "10",
    min_pct: str = "0.10",
    max_amount: str = "150",
    max_pct: str = "2",
) -> dict[str, str]:
    return {
        "min_amount_usd": min_amount,
        "min_pct_equity": min_pct,
        "max_amount_usd": max_amount,
        "max_pct_equity": max_pct,
    }


def payload() -> dict[str, object]:
    return {
        "default_profile": "mid",
        "min_amount_usd": "10",
        "min_pct_equity": "0.10",
        "max_amount_usd": "1000",
        "max_pct_equity": "5",
        "profiles": {
            "low": _limits(max_amount="50", max_pct="1"),
            "mid": _limits(),
            "high": _limits(max_amount="500", max_pct="5"),
        },
    }


def _client(
    session: Session,
    settings: Settings,
    *,
    token: str | None = None,
) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        BearerAuthMiddleware,
        settings=settings,
        session_factory=None,
    )
    app.include_router(settings_router.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    if token is not None:
        client.headers.update({"authorization": f"Bearer {token}"})
    return client


@pytest.fixture
def genesis(session: Session, settings: Settings) -> None:
    paper.ensure_genesis(session, settings.paper_starting_cash_usd)


def test_get_returns_config_defaults_and_server_previews(
    client_for, session: Session, settings: Settings, genesis: None
) -> None:
    client = client_for(settings_router.router)

    response = client.get("/api/v1/settings/trade-policy")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "default"
    assert body["default_profile"] == "mid"
    assert body["updated_at"] is None
    assert body["updated_by"] is None
    assert Decimal(body["equity_usd"]) == Decimal("10000")
    assert Decimal(body["previews"]["low"]["amount_usd"]) == Decimal("50")
    assert Decimal(body["previews"]["mid"]["amount_usd"]) == Decimal("150")
    assert Decimal(body["previews"]["high"]["amount_usd"]) == Decimal("500")
    assert body["previews"]["mid"]["reason"] is None


def test_put_requires_a_principal_even_when_reads_are_open(
    session: Session, settings: Settings, genesis: None
) -> None:
    protected = settings.model_copy(update={"api_token": SecretStr("correct-token")})
    client = _client(session, protected)

    assert client.get("/api/v1/settings/trade-policy").status_code == 200
    denied = client.put("/api/v1/settings/trade-policy", json=payload())
    wrong = client.put(
        "/api/v1/settings/trade-policy",
        json=payload(),
        headers={"authorization": "Bearer wrong"},
    )
    allowed = client.put(
        "/api/v1/settings/trade-policy",
        json=payload(),
        headers={"authorization": "Bearer correct-token"},
    )

    assert denied.status_code == 401
    assert wrong.status_code == 401
    assert allowed.status_code == 200
    # The middleware's principal, not a client-supplied name, is the audit actor.
    assert allowed.json()["updated_by"] == "deployment"


def test_put_persists_one_audited_row_outside_the_ledger(
    client_for, session: Session, settings: Settings, genesis: None
) -> None:
    client = client_for(settings_router.router)
    body = payload()
    body["default_profile"] = "high"
    body["profiles"]["mid"] = _limits(max_amount="200")

    saved = client.put("/api/v1/settings/trade-policy", json=body)

    assert saved.status_code == 200
    stored = saved.json()
    assert stored["source"] == "persisted"
    assert stored["default_profile"] == "high"
    assert Decimal(stored["profiles"]["mid"]["max_amount_usd"]) == Decimal("200")
    assert stored["updated_at"] is not None
    assert stored["updated_by"] == "unauthenticated-dev"
    assert Decimal(stored["previews"]["mid"]["amount_usd"]) == Decimal("200")
    rows = list(session.scalars(select(AppSettingRow)))
    assert len(rows) == 1
    assert rows[0].key == "trade_policy"
    # Genesis wrote two ledger legs; saving a setting wrote none.
    ledger = list(session.scalars(select(LedgerEntryRow)))
    assert len(ledger) == 2
    assert all(row.kind != "settings" for row in ledger)

    read = client.get("/api/v1/settings/trade-policy")
    assert read.status_code == 200
    assert read.json()["source"] == "persisted"


def test_put_rejects_cross_field_and_profile_errors(
    client_for, session: Session, settings: Settings, genesis: None
) -> None:
    client = client_for(settings_router.router)

    below = payload()
    below["profiles"]["mid"] = _limits(min_amount="200", max_amount="100")
    assert client.put("/api/v1/settings/trade-policy", json=below).status_code == 422

    pct = payload()
    pct["profiles"]["mid"]["max_pct_equity"] = "101"
    assert client.put("/api/v1/settings/trade-policy", json=pct).status_code == 422

    negative = payload()
    negative["min_amount_usd"] = "-1"
    assert client.put("/api/v1/settings/trade-policy", json=negative).status_code == 422

    outside = payload()
    outside["profiles"]["mid"] = _limits(max_amount="2000")
    response = client.put("/api/v1/settings/trade-policy", json=outside)
    assert response.status_code == 422
    assert "PROFILE_OUTSIDE_ENVELOPE" in response.text

    extra = payload()
    profiles = dict(extra["profiles"])  # type: ignore[arg-type]
    profiles["aggressive"] = _limits(max_amount="40", max_pct="1")
    extra["profiles"] = profiles
    assert client.put("/api/v1/settings/trade-policy", json=extra).status_code == 422


def test_preview_is_read_only_and_uses_a_draft_profile(
    client_for, session: Session, settings: Settings, genesis: None
) -> None:
    client = client_for(settings_router.router)

    response = client.get(
        "/api/v1/settings/trade-policy/preview",
        params={
            "profile": "mid",
            "max_amount_usd": "75",
            "manual_amount_usd": "37.42",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert Decimal(body["amount_usd"]) == Decimal("37.42")
    assert body["manual"] is True
    assert body["reason"] is None
    assert Decimal(body["limits"]["max_amount_usd"]) == Decimal("75")
    assert session.scalar(select(AppSettingRow)) is None

    automatic = client.get(
        "/api/v1/settings/trade-policy/preview",
        params={"profile": "mid", "max_amount_usd": "75"},
    )
    assert Decimal(automatic.json()["amount_usd"]) == Decimal("75")

    invalid = client.get(
        "/api/v1/settings/trade-policy/preview",
        params={"profile": "mid", "min_amount_usd": "100", "max_amount_usd": "50"},
    )
    assert invalid.status_code == 422

    outside_envelope = client.get(
        "/api/v1/settings/trade-policy/preview",
        params={
            "profile": "mid",
            "max_amount_usd": "150",
            "envelope_max_amount_usd": "75",
        },
    )
    assert outside_envelope.status_code == 422
    assert "PROFILE_OUTSIDE_ENVELOPE" in outside_envelope.text


def test_preview_quantizes_a_derived_size_to_cents(
    client_for, session: Session, settings: Settings, genesis: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "ezchain_api.services.account_read.equity_usd",
        lambda _session: Decimal("7003.33"),
    )
    client = client_for(settings_router.router)

    # 2% of $7,000 is $140 exactly; $7,003.33 makes it $140.0666, so the order path
    # sends $140.06, never $140.07.
    response = client.get(
        "/api/v1/settings/trade-policy/preview", params={"profile": "mid"}
    )

    assert response.status_code == 200
    assert Decimal(response.json()["amount_usd"]) == Decimal("140.06")


def test_a_malformed_stored_policy_falls_back_to_config_defaults(
    client_for, session: Session, settings: Settings, genesis: None
) -> None:
    session.add(
        AppSettingRow(
            key="trade_policy",
            value={"profiles": {}, "min_amount_usd": "0"},
            updated_by="broken-writer",
            updated_at=datetime.now(UTC),
        )
    )
    session.commit()
    client = client_for(settings_router.router)

    response = client.get("/api/v1/settings/trade-policy")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "default"
    assert Decimal(body["profiles"]["mid"]["max_amount_usd"]) == Decimal("150")
    assert body["updated_by"] == "broken-writer"


def test_quantize_resolution_rounds_down_and_never_crosses_a_bound() -> None:
    limits = TradeSizeLimits(
        min_amount_usd=Decimal("10"),
        min_pct_equity=Decimal("0"),
        max_amount_usd=Decimal("1000"),
        max_pct_equity=Decimal("100"),
    )
    derived = TradeSizeResolution(
        amount_usd=Decimal("9.999"),
        profile="mid",
        limits=limits,
        equity_usd=Decimal("10000"),
    )
    manual = TradeSizeResolution(
        amount_usd=Decimal("10.00"),
        profile="mid",
        limits=limits,
        equity_usd=Decimal("10000"),
        manual=True,
    )

    refused = quantize_resolution(derived)
    assert refused.amount_usd is None
    assert refused.reason == "SIZE_BELOW_MIN"
    assert quantize_resolution(manual).amount_usd == Decimal("10.00")
    assert quantize_resolution(manual).manual is True


def test_preview_reports_a_stable_reason_without_equity(
    client_for, session: Session, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "ezchain_api.services.account_read.equity_usd", lambda _session: None
    )
    client = client_for(settings_router.router)

    response = client.get(
        "/api/v1/settings/trade-policy/preview", params={"profile": "mid"}
    )

    assert response.status_code == 200
    assert response.json()["amount_usd"] is None
    assert response.json()["reason"] == "EQUITY_UNKNOWN"
