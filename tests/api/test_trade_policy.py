"""The trade-policy surface: one resolver, both faces, stable codes.

The order gate is the truth. These tests pin that the policy document renders the fields
the gate reads, that the preview uses the same sizing helper, and that a per-strategy
override is validated, disclosed when it loosens, and audited.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_api.config import Settings, get_settings
from ezchain_api.models import StrategyRow, StrategyRunRow
from ezchain_api.routers import trade_policy as trade_policy_router
from ezchain_api.services import trade_policy as service
from ezchain_core.risk import RiskLimits, size_reasons
from ezchain_core.trade_policy import (
    POLICY_FIELD_KEYS,
    PROFILE_ORDER,
    TRADE_RISK_PROFILES,
    preview_size,
    validate_registry,
)

WEB_KEYS = Path(__file__).resolve().parents[2] / "apps/web/lib/trade-policy-keys.ts"


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "data_mode": "fixture",
        "database_url": "sqlite+pysqlite://",
        "auto_create_schema": False,
        "max_position_usd": Decimal("2500"),
    }
    values.update(overrides)
    return Settings(**values)


def _client(client_for: Any, settings: Settings) -> TestClient:
    return client_for(trade_policy_router.router, {get_settings: lambda: settings})


def _add_strategy(
    session: Session,
    *,
    name: str = "test",
    params: dict[str, Any] | None = None,
    risk_profile: str | None = None,
) -> str:
    row = StrategyRow(
        name=name,
        chain="solana",
        token_address="So11111111111111111111111111111111111111112",
        token_symbol="TEST",
        kind="momentum",
        params=params or {},
        status="active",
        risk_profile=risk_profile,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.id


def test_the_document_lists_every_profile_with_every_field(
    client_for: Any, session: Session
) -> None:
    client = _client(client_for, _settings(trade_risk_profile="mid"))

    response = client.get("/api/v1/trade-policy")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "paper"
    assert [profile["name"] for profile in body["profiles"]] == list(PROFILE_ORDER)
    for profile in body["profiles"]:
        assert [field["key"] for field in profile["fields"]] == list(POLICY_FIELD_KEYS)
        for field in profile["fields"]:
            assert field["label"]
            assert field["description"]
            assert Decimal(field["minimum"]) <= Decimal(field["maximum"])
            assert field["value"] is not None


def test_the_active_profile_and_its_fields_are_the_enforced_values(
    client_for: Any, session: Session
) -> None:
    client = _client(client_for, _settings(trade_risk_profile="low"))
    mid = TRADE_RISK_PROFILES["mid"]

    body = client.get("/api/v1/trade-policy").json()

    assert body["active_profile"] == "low"
    by_key = {field["key"]: field["value"] for field in body["fields"]}
    assert by_key == {
        "min_order_usd": "5",
        "max_order_usd": "50",
        "max_order_pct_of_equity": "0.02",
        "per_token_cap_usd": "100",
        "max_slippage_bps": 100,
    }
    # The same numbers the order gate reads for that profile.
    assert Decimal(by_key["max_order_usd"]) == TRADE_RISK_PROFILES["low"].max_order_usd
    assert mid.min_order_usd < mid.max_order_usd


def test_every_profile_is_representable() -> None:
    """The issue's "$5 minimum / $100 or 5% whichever is lower" is exactly ``mid``."""
    validate_registry()
    mid = TRADE_RISK_PROFILES["mid"]
    assert mid.min_order_usd == Decimal("5")
    assert mid.max_order_usd == Decimal("100")
    assert mid.max_order_pct_of_equity == Decimal("0.05")
    for profile in TRADE_RISK_PROFILES.values():
        assert profile.min_order_usd <= profile.max_order_usd
        assert Decimal("0") <= profile.max_order_pct_of_equity <= Decimal("1")


def test_a_preview_uses_the_order_gate_resolver() -> None:
    limits = RiskLimits(
        min_order_usd=TRADE_RISK_PROFILES["mid"].min_order_usd,
        max_order_usd=TRADE_RISK_PROFILES["mid"].max_order_usd,
        max_order_pct_of_equity=TRADE_RISK_PROFILES["mid"].max_order_pct_of_equity,
    )
    equity = Decimal("2000")
    for amount in (Decimal("4"), Decimal("5"), Decimal("99"), Decimal("101")):
        expected = [reason.code for reason in size_reasons(amount, limits, equity)]
        actual = preview_size("mid", amount, equity)
        assert [reason.code for reason in actual.reasons] == expected


def test_the_percent_of_equity_cap_is_whichever_is_lower() -> None:
    result = preview_size("mid", Decimal("60"), Decimal("1000"))

    assert result.allowed is False
    assert result.percent_cap_usd == Decimal("50.00")
    assert result.effective_max_usd == Decimal("50.00")
    assert [reason.code for reason in result.reasons] == ["SIZE_ABOVE_MAX"]


def test_an_exact_boundary_is_allowed() -> None:
    at_cap = preview_size("mid", Decimal("50"), Decimal("1000"))
    over_cap = preview_size("mid", Decimal("50.01"), Decimal("1000"))

    assert at_cap.allowed is True
    assert over_cap.allowed is False


def test_preview_reports_each_stable_code(client_for: Any) -> None:
    client = _client(client_for, _settings(trade_risk_profile="mid"))

    below = client.get(
        "/api/v1/trade-policy/preview",
        params={"profile": "mid", "amount_usd": "1", "equity_usd": "1000"},
    )
    assert below.status_code == 200
    assert "SIZE_BELOW_MIN" in [reason["code"] for reason in below.json()["reasons"]]

    unknown_equity = client.get(
        "/api/v1/trade-policy/preview",
        params={"profile": "mid", "amount_usd": "10"},
    )
    assert unknown_equity.status_code == 200
    assert "EQUITY_UNKNOWN" in [reason["code"] for reason in unknown_equity.json()["reasons"]]

    unknown_profile = client.get(
        "/api/v1/trade-policy/preview",
        params={"profile": "turbo", "amount_usd": "10", "equity_usd": "1000"},
    )
    assert unknown_profile.status_code == 422
    assert unknown_profile.json()["detail"]["code"] == "PROFILE_UNKNOWN"


def test_the_default_profile_is_used_when_the_preview_names_none(client_for: Any) -> None:
    client = _client(client_for, _settings(trade_risk_profile="high"))

    body = client.get(
        "/api/v1/trade-policy/preview",
        params={"amount_usd": "10", "equity_usd": "1000"},
    ).json()

    assert body["profile"] == "high"


def test_a_strategy_can_carry_its_own_profile_and_the_change_is_audited(
    client_for: Any, session: Session
) -> None:
    strategy_id = _add_strategy(session, name="aggressive", params={"order_usd": "10"})
    client = _client(client_for, _settings(trade_risk_profile="low"))

    response = client.put(
        f"/api/v1/trade-policy/strategies/{strategy_id}",
        json={"risk_profile": "high", "acknowledge_risk": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["risk_profile"] == "high"
    assert body["effective_profile"] == "high"
    assert body["source"] == "strategy"

    run = session.scalars(
        select(StrategyRunRow).where(StrategyRunRow.strategy_id == strategy_id)
    ).one()
    assert run.reason == service.AUDIT_REASON
    assert run.inputs["before"] is None
    assert run.inputs["after"] == "high"


def test_loosening_a_strategy_profile_needs_acknowledgement(
    client_for: Any, session: Session
) -> None:
    strategy_id = _add_strategy(session, risk_profile="low")
    client = _client(client_for, _settings(trade_risk_profile="low"))

    refused = client.put(
        f"/api/v1/trade-policy/strategies/{strategy_id}",
        json={"risk_profile": "high"},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "RISK_ACK_REQUIRED"
    assert session.get(StrategyRow, strategy_id).risk_profile == "low"

    accepted = client.put(
        f"/api/v1/trade-policy/strategies/{strategy_id}",
        json={"risk_profile": "high", "acknowledge_risk": True},
    )
    assert accepted.status_code == 200
    assert accepted.json()["risk_profile"] == "high"


def test_tightening_a_strategy_profile_needs_no_acknowledgement(
    client_for: Any, session: Session
) -> None:
    strategy_id = _add_strategy(session, risk_profile="high")
    client = _client(client_for, _settings(trade_risk_profile="high"))

    response = client.put(
        f"/api/v1/trade-policy/strategies/{strategy_id}",
        json={"risk_profile": "low"},
    )

    assert response.status_code == 200
    assert response.json()["effective_profile"] == "low"


def test_opting_into_a_profile_where_there_was_none_is_a_tightening(
    client_for: Any, session: Session
) -> None:
    strategy_id = _add_strategy(session)
    client = _client(client_for, _settings(trade_risk_profile=None))

    response = client.put(
        f"/api/v1/trade-policy/strategies/{strategy_id}",
        json={"risk_profile": "mid"},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "strategy"


def test_opting_into_a_profile_above_the_deployment_ceiling_needs_the_ack(
    client_for: Any, session: Session
) -> None:
    """With no deployment profile, its own limits are the "before" a change is measured on.

    A deployment capped at $100 per order is loosened by ``high`` ($250), so accepting it
    without the acknowledgement would silently widen a fund-affecting limit.
    """
    strategy_id = _add_strategy(session)
    client = _client(
        client_for,
        _settings(trade_risk_profile=None, max_order_usd=Decimal("100")),
    )

    refused = client.put(
        f"/api/v1/trade-policy/strategies/{strategy_id}",
        json={"risk_profile": "high"},
    )

    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "RISK_ACK_REQUIRED"
    assert session.get(StrategyRow, strategy_id).risk_profile is None

    accepted = client.put(
        f"/api/v1/trade-policy/strategies/{strategy_id}",
        json={"risk_profile": "high", "acknowledge_risk": True},
    )
    assert accepted.status_code == 200
    assert accepted.json()["risk_profile"] == "high"


def test_a_profile_inside_the_deployment_ceiling_is_still_an_opt_in(
    client_for: Any, session: Session
) -> None:
    """A profile whose ceiling cannot exceed the deployment's adds gates only."""
    strategy_id = _add_strategy(session)
    client = _client(
        client_for,
        _settings(trade_risk_profile=None, max_order_usd=Decimal("1000")),
    )

    response = client.put(
        f"/api/v1/trade-policy/strategies/{strategy_id}",
        json={"risk_profile": "mid"},
    )

    assert response.status_code == 200
    assert response.json()["risk_profile"] == "mid"


def test_the_audit_records_both_values_the_actor_and_the_acknowledgement(
    client_for: Any, session: Session
) -> None:
    strategy_id = _add_strategy(session, risk_profile="low")
    client = _client(client_for, _settings(trade_risk_profile="low"))

    response = client.put(
        f"/api/v1/trade-policy/strategies/{strategy_id}",
        json={"risk_profile": "high", "acknowledge_risk": True},
    )
    assert response.status_code == 200

    run = session.scalars(
        select(StrategyRunRow).where(StrategyRunRow.strategy_id == strategy_id)
    ).one()
    assert run.inputs["before"] == "low"
    assert run.inputs["after"] == "high"
    assert run.inputs["before_effective"] == "low"
    assert run.inputs["after_effective"] == "high"
    assert run.inputs["acknowledged"] is True
    assert "actor" in run.inputs


def test_clearing_an_override_that_widened_the_ceiling_needs_the_ack(
    client_for: Any, session: Session
) -> None:
    """Clearing restores the deployment ceiling, so it loosens when that ceiling is wider."""
    strategy_id = _add_strategy(session, risk_profile="high")
    client = _client(
        client_for,
        _settings(trade_risk_profile=None, max_order_usd=Decimal("1000")),
    )

    refused = client.put(
        f"/api/v1/trade-policy/strategies/{strategy_id}",
        json={"risk_profile": None},
    )

    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "RISK_ACK_REQUIRED"
    assert session.get(StrategyRow, strategy_id).risk_profile == "high"

    accepted = client.put(
        f"/api/v1/trade-policy/strategies/{strategy_id}",
        json={"risk_profile": None, "acknowledge_risk": True},
    )
    assert accepted.status_code == 200
    assert accepted.json()["risk_profile"] is None


def test_clearing_an_override_returns_the_strategy_to_the_default(
    client_for: Any, session: Session
) -> None:
    strategy_id = _add_strategy(session, risk_profile="high")
    client = _client(client_for, _settings(trade_risk_profile="mid"))

    response = client.put(
        f"/api/v1/trade-policy/strategies/{strategy_id}",
        json={"risk_profile": None},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "default"
    assert response.json()["risk_profile"] is None
    assert response.json()["effective_profile"] == "mid"


def test_unknown_strategy_and_profile_are_typed_refusals(client_for: Any, session: Session) -> None:
    strategy_id = _add_strategy(session)
    client = _client(client_for, _settings(trade_risk_profile="mid"))

    missing = client.put(
        "/api/v1/trade-policy/strategies/does-not-exist",
        json={"risk_profile": "low"},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "STRATEGY_NOT_FOUND"

    typo = client.put(
        f"/api/v1/trade-policy/strategies/{strategy_id}",
        json={"risk_profile": "turbo"},
    )
    assert typo.status_code == 422
    assert typo.json()["detail"]["code"] == "PROFILE_UNKNOWN"


def test_a_strategy_sizing_above_its_profile_is_named_as_a_warning(
    client_for: Any, session: Session
) -> None:
    _add_strategy(
        session,
        name="too-big",
        params={"order_usd": "300", "max_position_usd": "400"},
        risk_profile="mid",
    )
    client = _client(client_for, _settings(trade_risk_profile="mid"))

    row = client.get("/api/v1/trade-policy").json()["strategies"][0]

    codes = [warning["code"] for warning in row["warnings"]]
    assert "SIZE_ABOVE_MAX" in codes
    assert "POSITION_LIMIT" in codes


def test_the_api_and_the_web_surface_declare_the_same_fields() -> None:
    """OWNER-DECISIONS §5: an API-only field or a GUI-only control fails the build."""
    rendered_fields, rendered_profiles = _web_surface()

    assert tuple(rendered_fields) == POLICY_FIELD_KEYS
    assert tuple(rendered_profiles) == PROFILE_ORDER


def _web_surface() -> tuple[list[str], list[str]]:
    node = shutil.which("node")
    assert node, "node is required: the dual-surface guard evaluates the web field list"
    script = (
        "const m = await import(process.argv[1]);"
        "process.stdout.write(JSON.stringify({"
        "fields: m.TRADE_POLICY_FIELD_KEYS ?? null,"
        "profiles: m.TRADE_POLICY_PROFILE_ORDER ?? null"
        "}));"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", script, str(WEB_KEYS)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload["fields"], list) and payload["fields"]
    assert isinstance(payload["profiles"], list) and payload["profiles"]
    return [str(key) for key in payload["fields"]], [str(name) for name in payload["profiles"]]


@pytest.mark.parametrize("profile", list(PROFILE_ORDER))
def test_every_profile_has_a_min_max_and_percent_bound(profile: str) -> None:
    definition = TRADE_RISK_PROFILES[profile]
    assert definition.min_order_usd > 0
    assert definition.max_order_usd >= definition.min_order_usd
    assert Decimal("0") <= definition.max_order_pct_of_equity <= Decimal("1")
