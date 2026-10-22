"""CLI parsing and safety refusals; no network, no gh, no API."""

from __future__ import annotations

from decimal import Decimal

import pytest
from tools.stress.cli import _parser, build_config, main
from tools.stress.config import ConfigError


def config(*argv: str):
    return build_config(_parser().parse_args(list(argv)))


def test_strategy_order_usd_is_parsed() -> None:
    parsed = config("--strategy-order-usd", "1500")
    assert parsed.strategy_order_usd == Decimal("1500")
    assert parsed.order_usd == Decimal("1")


@pytest.mark.parametrize("value", ["0", "-1", "nan", "not-a-number"])
def test_strategy_order_usd_must_be_positive_and_finite(value: str) -> None:
    with pytest.raises(ConfigError):
        config("--strategy-order-usd", value)


def test_issue_filing_requires_a_repo() -> None:
    with pytest.raises(ConfigError):
        config("--file-issues")
    with pytest.raises(ConfigError):
        config("--dry-run-issues")


def test_credentials_in_base_url_are_refused() -> None:
    with pytest.raises(ConfigError):
        config("--base-url", "http://user:pass@127.0.0.1:8000")


def test_self_test_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--self-test"]) == 0
    assert "self-test: ok" in capsys.readouterr().out


def test_config_error_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--base-url", "http://user:pass@127.0.0.1:8000"]) == 2
    assert "CONFIG_ERROR" in capsys.readouterr().err
