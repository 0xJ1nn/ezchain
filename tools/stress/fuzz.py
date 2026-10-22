"""Invalid-mutation fuzz matrix: the case table and its verdicts.

Every case is a request a client should never be able to make work. The contract the
harness enforces is deliberately narrow and mechanical:

* a rejected mutation answers ``4xx`` with a JSON error body, never ``5xx``;
* an accepted mutation is only acceptable where the API documents acceptance (unknown
  fields are ignored by Pydantic by design — see ``apps/api/src/ezchain_api/schemas.py``);
* no rejected mutation creates an order, and therefore no rejected mutation fills.

The table is data; the verdicts are pure functions. Both are unit-tested without a server.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from .checks import Finding, _mapping

Expectation = Literal["reject", "tolerate"]

# Not a credential: a deliberately wrong bearer value for the auth-negative cases.
WRONG_TOKEN = "stress-wrong-token-0000000000000000"


@dataclass(frozen=True)
class FuzzCase:
    """One invalid mutation.

    ``expect='reject'`` requires a 4xx with a JSON error body. ``expect='tolerate'``
    documents a shape the API accepts on purpose and only asserts no 5xx.
    """

    case_id: str
    description: str
    path: str
    method: str = "POST"
    body: Mapping[str, object] | None = None
    raw_body: str | None = None
    content_type: str = "application/json"
    expect: Expectation = "reject"
    tolerate_reason: str = ""

    @property
    def expects_rejection(self) -> bool:
        return self.expect == "reject"


@dataclass(frozen=True)
class FuzzOutcome:
    """What one case actually did, ready for the report table."""

    case_id: str
    description: str
    expected: str
    status: int | None
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "description": self.description,
            "expected": self.expected,
            "status": self.status,
            "ok": self.ok,
            "detail": self.detail,
        }


def _order_body(
    *,
    chain: str,
    token: str,
    key: str,
    side: str = "buy",
    amount: object = "1",
) -> dict[str, object]:
    body: dict[str, object] = {
        "chain": chain,
        "token_address": token,
        "side": side,
        "amount_usd": amount,
        "idempotency_key": key,
    }
    return body


def _strategy_body(
    *,
    chain: str,
    token: str,
    symbol: str,
    kind: str,
    params: Mapping[str, object],
    name: str,
) -> dict[str, object]:
    return {
        "name": name,
        "chain": chain,
        "token_address": token,
        "token_symbol": symbol,
        "kind": kind,
        "params": dict(params),
        "status": "active",
    }


def order_fuzz_cases(*, chain: str, token: str, key_prefix: str) -> list[FuzzCase]:
    """Invalid ``POST /api/v1/paper/orders`` payloads."""

    def key(name: str) -> str:
        return f"{key_prefix}-{name}"

    cases: list[FuzzCase] = [
        FuzzCase(
            "amount_negative",
            "negative amount_usd",
            "/api/v1/paper/orders",
            body=_order_body(chain=chain, token=token, key=key("neg"), amount="-1"),
        ),
        FuzzCase(
            "amount_zero",
            "zero amount_usd",
            "/api/v1/paper/orders",
            body=_order_body(chain=chain, token=token, key=key("zero"), amount="0"),
        ),
        FuzzCase(
            "amount_nan",
            "amount_usd NaN",
            "/api/v1/paper/orders",
            body=_order_body(chain=chain, token=token, key=key("nan"), amount="NaN"),
        ),
        FuzzCase(
            "amount_infinity",
            "amount_usd Infinity",
            "/api/v1/paper/orders",
            body=_order_body(chain=chain, token=token, key=key("inf"), amount="Infinity"),
        ),
        FuzzCase(
            "amount_float_overflow",
            "amount_usd 1e400 (JSON float overflow)",
            "/api/v1/paper/orders",
            raw_body=(
                '{"chain": '
                + json.dumps(chain)
                + ', "token_address": '
                + json.dumps(token)
                + ', "side": "buy", "amount_usd": 1e400, "idempotency_key": '
                + json.dumps(key("overflow"))
                + "}"
            ),
        ),
        FuzzCase(
            "amount_empty",
            "empty amount_usd",
            "/api/v1/paper/orders",
            body=_order_body(chain=chain, token=token, key=key("empty"), amount=""),
        ),
        FuzzCase(
            "side_hold",
            "side hold",
            "/api/v1/paper/orders",
            body=_order_body(chain=chain, token=token, key=key("hold"), side="hold"),
        ),
        FuzzCase(
            "chain_unknown",
            "unknown chain",
            "/api/v1/paper/orders",
            body=_order_body(chain="stress-no-such-chain", token=token, key=key("chain")),
        ),
        FuzzCase(
            "token_address_10kb",
            "10 kB token address",
            "/api/v1/paper/orders",
            body=_order_body(chain=chain, token="0x" + "a" * 10_000, key=key("bigaddr")),
        ),
        FuzzCase(
            "idempotency_missing",
            "missing idempotency_key",
            "/api/v1/paper/orders",
            body={
                "chain": chain,
                "token_address": token,
                "side": "buy",
                "amount_usd": "1",
            },
        ),
        FuzzCase(
            "idempotency_overlong",
            "200-character idempotency_key",
            "/api/v1/paper/orders",
            body=_order_body(chain=chain, token=token, key="k" * 200),
        ),
        FuzzCase(
            "idempotency_non_ascii",
            "non-ascii idempotency_key",
            "/api/v1/paper/orders",
            body=_order_body(chain=chain, token=token, key=f"{key('unicode')}-ünïcode-Ω"),
        ),
        FuzzCase(
            "idempotency_short",
            "7-character idempotency_key",
            "/api/v1/paper/orders",
            body=_order_body(chain=chain, token=token, key="short77"),
        ),
        FuzzCase(
            "unknown_fields",
            "extra unknown fields (strategy_id injection attempt)",
            "/api/v1/paper/orders",
            body={
                **_order_body(chain=chain, token=token, key=key("extra")),
                "strategy_id": "stress-injected-attribution",
                "evil": True,
            },
            expect="tolerate",
            tolerate_reason="Pydantic ignores unknown fields by design (see schemas.py)",
        ),
        FuzzCase(
            "wrong_content_type",
            "valid JSON body sent as text/plain",
            "/api/v1/paper/orders",
            raw_body=json.dumps(_order_body(chain=chain, token=token, key=key("ctype"))),
            content_type="text/plain",
        ),
        FuzzCase(
            "body_not_json",
            "body is not JSON",
            "/api/v1/paper/orders",
            raw_body="this is not json",
        ),
    ]
    return cases


def strategy_fuzz_cases(*, chain: str, token: str, symbol: str, key_prefix: str) -> list[FuzzCase]:
    """Invalid strategy create/patch/delete requests."""

    def body(kind: str, params: Mapping[str, object], name: str) -> dict[str, object]:
        return _strategy_body(
            chain=chain, token=token, symbol=symbol, kind=kind, params=params, name=name
        )

    return [
        FuzzCase(
            "strategy_kind_typo",
            "unknown strategy kind",
            "/api/v1/strategies",
            body=body("momemtum", {}, f"{key_prefix}-kind"),
        ),
        FuzzCase(
            "strategy_param_typo",
            "typo'd parameter key",
            "/api/v1/strategies",
            body=body("dca", {"order_usdd": "1"}, f"{key_prefix}-paramtypo"),
        ),
        FuzzCase(
            "strategy_interval_zero",
            "interval_minutes = 0",
            "/api/v1/strategies",
            body=body("dca", {"interval_minutes": 0}, f"{key_prefix}-interval"),
        ),
        FuzzCase(
            "strategy_negative_order",
            "negative order_usd",
            "/api/v1/strategies",
            body=body("dca", {"order_usd": "-5"}, f"{key_prefix}-negorder"),
        ),
        FuzzCase(
            "strategy_lookback_unsupported",
            "unsupported lookback",
            "/api/v1/strategies",
            body=body("momentum", {"lookback": "3h"}, f"{key_prefix}-lookback"),
        ),
        FuzzCase(
            "strategy_threshold_inverted",
            "sell_above_usd <= buy_below_usd",
            "/api/v1/strategies",
            body=body(
                "threshold",
                {"buy_below_usd": "2", "sell_above_usd": "1"},
                f"{key_prefix}-inverted",
            ),
        ),
        FuzzCase(
            "strategy_threshold_missing",
            "threshold strategy without its thresholds",
            "/api/v1/strategies",
            body=body("threshold", {}, f"{key_prefix}-missing"),
        ),
        FuzzCase(
            "strategy_patch_unknown",
            "PATCH an unknown strategy id",
            "/api/v1/strategies/stress-does-not-exist",
            method="PATCH",
            body={"status": "paused"},
        ),
        FuzzCase(
            "strategy_patch_invalid_status",
            "PATCH with an invalid status",
            "/api/v1/strategies/stress-does-not-exist",
            method="PATCH",
            body={"status": "teleported"},
        ),
        FuzzCase(
            "strategy_delete_unknown",
            "DELETE an unknown strategy id",
            "/api/v1/strategies/stress-does-not-exist",
            method="DELETE",
        ),
    ]


def _error_body_ok(payload: Mapping[str, object] | None) -> bool:
    """The project's error envelope is ``{"detail": ...}`` for every 4xx."""
    return payload is not None and payload.get("detail") is not None


def check_fuzz_response(
    case: FuzzCase,
    *,
    status: int | None,
    payload: Mapping[str, object] | None,
    raw_text: str,
    orders_before: int,
    orders_after: int,
    scenario: str,
) -> tuple[FuzzOutcome, list[Finding]]:
    """Judge one fuzz request. Returns the report row and any findings."""
    expected = "4xx + JSON error body" if case.expects_rejection else "no 5xx"
    evidence = raw_text[:300]
    findings: list[Finding] = []
    detail = ""

    if status is None:
        detail = "transport error"
        outcome = FuzzOutcome(
            case_id=case.case_id,
            description=case.description,
            expected=expected,
            status=None,
            ok=False,
            detail=detail,
        )
        return outcome, findings

    # A 5xx is never acceptable, whatever the case expects. The transport layer already
    # records ``http.no_unexpected_5xx``; this names the exact fuzz case instead.
    if 500 <= status <= 599:
        findings.append(
            Finding(
                code="FUZZ_SERVER_ERROR",
                scenario=scenario,
                invariant="fuzz.no_server_error",
                summary=f"fuzz case {case.case_id} produced a {status}",
                expected="4xx with a JSON error body",
                actual=f"HTTP {status}",
                evidence=evidence,
            )
        )
        return (
            FuzzOutcome(
                case_id=case.case_id,
                description=case.description,
                expected=expected,
                status=status,
                ok=False,
                detail=f"HTTP {status}",
            ),
            findings,
        )

    rejected = 400 <= status <= 499
    if rejected and not _error_body_ok(payload):
        findings.append(
            Finding(
                code="FUZZ_ERROR_BODY_NOT_JSON",
                scenario=scenario,
                invariant="fuzz.error_body",
                summary=f"fuzz case {case.case_id} rejected without a JSON error body",
                expected='4xx body shaped {"detail": ...}',
                actual=f"HTTP {status}",
                evidence=evidence,
            )
        )
        detail = "4xx without JSON error body"
        ok = False
    elif rejected:
        detail = f"HTTP {status}"
        ok = True
    elif case.expects_rejection:
        # 2xx/3xx for a mutation that must be refused.
        created = max(0, orders_after - orders_before)
        findings.append(
            Finding(
                code="FUZZ_ACCEPTED_INVALID",
                scenario=scenario,
                invariant="fuzz.rejects_invalid",
                summary=f"fuzz case {case.case_id} was accepted ({case.description})",
                expected="4xx with a JSON error body",
                actual=f"HTTP {status}",
                evidence=evidence,
            )
        )
        if created:
            findings.append(
                Finding(
                    code="FUZZ_FILLED_ON_INVALID",
                    scenario=scenario,
                    invariant="fuzz.no_fill",
                    summary=f"fuzz case {case.case_id} created {created} paper order(s)",
                    expected="an invalid mutation never creates an order",
                    actual=f"{created} order(s) created",
                    evidence=evidence,
                )
            )
        detail = f"HTTP {status} (accepted)"
        ok = False
    else:
        detail = f"HTTP {status} (tolerated: {case.tolerate_reason})"
        ok = True

    if case.expects_rejection and max(0, orders_after - orders_before) and rejected:
        # A 4xx that still created a row is a rejection that leaked work.
        findings.append(
            Finding(
                code="FUZZ_FILLED_ON_INVALID",
                scenario=scenario,
                invariant="fuzz.no_fill",
                summary=f"fuzz case {case.case_id} rejected but created an order",
                expected="a rejected mutation writes nothing",
                actual="an order row appeared",
                evidence=evidence,
            )
        )

    return (
        FuzzOutcome(
            case_id=case.case_id,
            description=case.description,
            expected=expected,
            status=status,
            ok=ok,
            detail=detail,
        ),
        findings,
    )


def check_delete_twice(
    first_status: int | None,
    second_status: int | None,
    *,
    scenario: str,
) -> list[Finding]:
    """The first DELETE removes the row, the second is a clean 404."""
    findings: list[Finding] = []
    if first_status not in {200, 204}:
        findings.append(
            Finding(
                code="FUZZ_DELETE_FIRST",
                scenario=scenario,
                invariant="fuzz.delete_twice",
                summary="the first DELETE of a created strategy did not succeed",
                expected="HTTP 204",
                actual=f"HTTP {first_status}",
            )
        )
    if second_status != 404:
        findings.append(
            Finding(
                code="FUZZ_DELETE_SECOND",
                scenario=scenario,
                invariant="fuzz.delete_twice",
                summary="deleting an already-deleted strategy did not return 404",
                expected="HTTP 404 STRATEGY_NOT_FOUND",
                actual=f"HTTP {second_status}",
            )
        )
    return findings


def check_auth_response(
    kind: str,
    *,
    status: int | None,
    payload: Mapping[str, object] | None,
    raw_text: str,
    orders_before: int,
    orders_after: int,
    scenario: str,
) -> list[Finding]:
    """``kind`` is ``missing`` (no Authorization) or ``wrong`` (bad bearer value)."""
    findings: list[Finding] = []
    if status is None:
        return findings
    accepted = status < 400
    if kind == "missing" and status != 401:
        findings.append(
            Finding(
                code="AUTH_MISSING_TOKEN_ACCEPTED",
                scenario=scenario,
                invariant="auth.missing_token_rejected",
                summary="a mutation without Authorization was not rejected with 401",
                expected="HTTP 401",
                actual=f"HTTP {status}",
                evidence=raw_text[:300],
            )
        )
    if kind == "wrong" and status not in {401, 403}:
        findings.append(
            Finding(
                code="AUTH_WRONG_TOKEN_ACCEPTED",
                scenario=scenario,
                invariant="auth.wrong_token_rejected",
                summary="a mutation with a wrong bearer token was accepted",
                expected="HTTP 401 or 403",
                actual=f"HTTP {status}",
                evidence=raw_text[:300],
            )
        )
    if status >= 400 and not _error_body_ok(payload):
        findings.append(
            Finding(
                code="AUTH_ERROR_BODY_NOT_JSON",
                scenario=scenario,
                invariant="auth.error_body",
                summary="the auth rejection did not carry a JSON error body",
                expected='{"detail": ...}',
                actual=f"HTTP {status}",
                evidence=raw_text[:300],
            )
        )
    if accepted and orders_after > orders_before:
        findings.append(
            Finding(
                code="AUTH_UNAUTHENTICATED_ORDER",
                scenario=scenario,
                invariant="auth.no_mutation",
                summary="an unauthenticated request created a paper order",
                expected="an unauthenticated mutation writes nothing",
                actual=f"{orders_after - orders_before} order(s) created",
            )
        )
    return findings


def check_auth_control(status: int | None, *, scenario: str) -> list[Finding]:
    """The same body with the correct token must be accepted, or the 401 proves nothing."""
    if status is not None and 200 <= status <= 299:
        return []
    return [
        Finding(
            code="AUTH_CONTROL_REJECTED",
            scenario=scenario,
            invariant="auth.control_valid",
            summary="the control request with the configured token was not accepted",
            expected="HTTP 2xx for the same body with the correct bearer token",
            actual=f"HTTP {status}",
        )
    ]


def parse_error_payload(raw_text: str) -> Mapping[str, object] | None:
    """Parse a response body as a JSON object; ``None`` when it is not one."""
    try:
        parsed: object = json.loads(raw_text)
    except (ValueError, TypeError):
        return None
    return _mapping(parsed)


def summary(rows: Sequence[FuzzOutcome]) -> dict[str, int]:
    """Counts for the report: total cases, rejected, tolerated and failed."""
    return {
        "total": len(rows),
        "ok": sum(1 for row in rows if row.ok),
        "failed": sum(1 for row in rows if not row.ok),
    }


__all__ = [
    "WRONG_TOKEN",
    "FuzzCase",
    "FuzzOutcome",
    "check_auth_control",
    "check_auth_response",
    "check_delete_twice",
    "check_fuzz_response",
    "order_fuzz_cases",
    "parse_error_payload",
    "strategy_fuzz_cases",
    "summary",
]
