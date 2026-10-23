"""Fuzz matrix: the case table and the verdicts on what the API answered."""

from __future__ import annotations

import json

import pytest
from tools.stress.fuzz import (
    FuzzCase,
    check_auth_control,
    check_auth_response,
    check_delete_twice,
    check_fuzz_response,
    order_fuzz_cases,
    parse_error_payload,
    strategy_fuzz_cases,
    summary,
)

TOKEN = "0x00000000000000000000000000000000000000aa"
ORDER_CASES = order_fuzz_cases(chain="base", token=TOKEN, key_prefix="stress-test-1")
STRATEGY_CASES = strategy_fuzz_cases(
    chain="base", token=TOKEN, symbol="TKN", key_prefix="stress-test-1"
)
ALL_CASES = [*ORDER_CASES, *STRATEGY_CASES]


def case(case_id: str) -> FuzzCase:
    for item in ALL_CASES:
        if item.case_id == case_id:
            return item
    raise AssertionError(f"missing fuzz case {case_id}")


def body_of(case_id: str) -> dict[str, object]:
    body = case(case_id).body
    assert body is not None
    return dict(body)


def test_every_required_case_is_present() -> None:
    required = {
        "amount_negative",
        "amount_zero",
        "amount_nan",
        "amount_infinity",
        "amount_float_overflow",
        "amount_empty",
        "side_hold",
        "chain_unknown",
        "token_address_10kb",
        "idempotency_missing",
        "idempotency_overlong",
        "idempotency_non_ascii",
        "unknown_fields",
        "wrong_content_type",
        "strategy_kind_typo",
        "strategy_param_typo",
        "strategy_interval_zero",
        "strategy_threshold_inverted",
        "strategy_patch_unknown",
        "strategy_delete_unknown",
    }
    assert required <= {item.case_id for item in ALL_CASES}


def test_case_ids_are_unique() -> None:
    ids = [item.case_id for item in ALL_CASES]
    assert len(ids) == len(set(ids))


def test_order_cases_carry_unique_idempotency_keys() -> None:
    keys = [
        str(body_of(item.case_id)["idempotency_key"])
        for item in ORDER_CASES
        if "idempotency_key" in (item.body or {})
    ]
    assert len(keys) == len(set(keys))
    # The two boundary cases must be exactly out of the documented 8..120 window.
    assert len(str(body_of("idempotency_short")["idempotency_key"])) == 7
    assert len(str(body_of("idempotency_overlong")["idempotency_key"])) == 200
    for item in ORDER_CASES:
        key = (item.body or {}).get("idempotency_key")
        if key is None or item.case_id in {"idempotency_short", "idempotency_overlong"}:
            continue
        assert 8 <= len(str(key)) <= 120, item.case_id


def test_the_float_overflow_case_sends_a_real_json_overflow() -> None:
    raw = case("amount_float_overflow").raw_body
    assert raw is not None
    assert "1e400" in raw
    parsed = json.loads(raw)
    assert parsed["amount_usd"] == float("inf")
    assert parsed["idempotency_key"].startswith("stress-test-1")


def test_the_extra_field_case_is_documented_as_tolerated() -> None:
    extra = case("unknown_fields")
    assert extra.expect == "tolerate"
    assert "schemas.py" in extra.tolerate_reason
    assert extra.body is not None
    assert extra.body["strategy_id"] == "stress-injected-attribution"


def test_wrong_content_type_and_non_json_body_are_raw() -> None:
    assert case("wrong_content_type").content_type == "text/plain"
    assert case("wrong_content_type").raw_body is not None
    assert case("body_not_json").raw_body == "this is not json"


def test_a_4xx_with_a_json_error_body_passes() -> None:
    outcome, findings = check_fuzz_response(
        case("amount_nan"),
        status=422,
        payload={"detail": [{"msg": "finite"}]},
        raw_text='{"detail": []}',
        orders_before=0,
        orders_after=0,
        scenario="t",
    )
    assert findings == []
    assert outcome.ok
    assert outcome.status == 422


def test_a_4xx_without_a_json_error_body_fails() -> None:
    _, findings = check_fuzz_response(
        case("amount_nan"),
        status=400,
        payload=None,
        raw_text="Bad Request",
        orders_before=0,
        orders_after=0,
        scenario="t",
    )
    assert [finding.invariant for finding in findings] == ["fuzz.error_body"]


def test_a_5xx_is_never_acceptable() -> None:
    outcome, findings = check_fuzz_response(
        case("amount_float_overflow"),
        status=500,
        payload=None,
        raw_text="Internal Server Error",
        orders_before=0,
        orders_after=0,
        scenario="t",
    )
    assert [finding.invariant for finding in findings] == ["fuzz.no_server_error"]
    assert findings[0].code == "FUZZ_SERVER_ERROR"
    assert not outcome.ok


def test_an_accepted_invalid_mutation_fails_and_reports_the_fill() -> None:
    outcome, findings = check_fuzz_response(
        case("idempotency_non_ascii"),
        status=201,
        payload={"id": "o1", "status": "filled"},
        raw_text='{"id": "o1"}',
        orders_before=3,
        orders_after=4,
        scenario="t",
    )
    assert not outcome.ok
    assert {finding.invariant for finding in findings} == {"fuzz.rejects_invalid", "fuzz.no_fill"}


def test_a_rejection_that_still_creates_an_order_fails() -> None:
    _, findings = check_fuzz_response(
        case("side_hold"),
        status=422,
        payload={"detail": []},
        raw_text="{}",
        orders_before=1,
        orders_after=2,
        scenario="t",
    )
    assert [finding.invariant for finding in findings] == ["fuzz.no_fill"]


def test_a_tolerated_case_may_be_accepted() -> None:
    outcome, findings = check_fuzz_response(
        case("unknown_fields"),
        status=201,
        payload={"id": "o1", "status": "rejected"},
        raw_text='{"id": "o1"}',
        orders_before=0,
        orders_after=1,
        scenario="t",
    )
    assert findings == []
    assert outcome.ok


def test_a_tolerated_case_may_not_break_the_server() -> None:
    _, findings = check_fuzz_response(
        case("unknown_fields"),
        status=502,
        payload=None,
        raw_text="upstream",
        orders_before=0,
        orders_after=0,
        scenario="t",
    )
    assert [finding.invariant for finding in findings] == ["fuzz.no_server_error"]


def test_a_transport_error_is_reported_not_hidden() -> None:
    outcome, findings = check_fuzz_response(
        case("amount_nan"),
        status=None,
        payload=None,
        raw_text="",
        orders_before=0,
        orders_after=0,
        scenario="t",
    )
    assert findings == []
    assert not outcome.ok
    assert outcome.detail == "transport error"


def test_parse_error_payload_accepts_only_objects() -> None:
    assert parse_error_payload('{"detail": "x"}') == {"detail": "x"}
    assert parse_error_payload("[1, 2]") is None
    assert parse_error_payload("not json") is None
    assert parse_error_payload("") is None


def test_delete_twice_expects_204_then_404() -> None:
    assert check_delete_twice(204, 404, scenario="t") == []
    assert check_delete_twice(200, 404, scenario="t") == []
    assert [f.code for f in check_delete_twice(204, 200, scenario="t")] == ["FUZZ_DELETE_SECOND"]
    assert [f.code for f in check_delete_twice(404, 404, scenario="t")] == ["FUZZ_DELETE_FIRST"]
    assert [f.code for f in check_delete_twice(None, None, scenario="t")] == [
        "FUZZ_DELETE_FIRST",
        "FUZZ_DELETE_SECOND",
    ]


def test_auth_missing_token_must_be_401() -> None:
    assert (
        check_auth_response(
            "missing",
            status=401,
            payload={"detail": {"code": "UNAUTHORIZED"}},
            raw_text="{}",
            orders_before=0,
            orders_after=0,
            scenario="t",
        )
        == []
    )
    findings = check_auth_response(
        "missing",
        status=201,
        payload={"id": "o1"},
        raw_text="{}",
        orders_before=0,
        orders_after=1,
        scenario="t",
    )
    assert {finding.invariant for finding in findings} == {
        "auth.missing_token_rejected",
        "auth.no_mutation",
    }


def test_auth_wrong_token_accepts_401_or_403() -> None:
    for status in (401, 403):
        assert (
            check_auth_response(
                "wrong",
                status=status,
                payload={"detail": {"code": "UNAUTHORIZED"}},
                raw_text="{}",
                orders_before=0,
                orders_after=0,
                scenario="t",
            )
            == []
        )
    findings = check_auth_response(
        "wrong",
        status=200,
        payload={"id": "o1"},
        raw_text="{}",
        orders_before=0,
        orders_after=0,
        scenario="t",
    )
    assert [finding.invariant for finding in findings] == ["auth.wrong_token_rejected"]


def test_auth_rejection_needs_a_json_error_body() -> None:
    findings = check_auth_response(
        "missing",
        status=401,
        payload=None,
        raw_text="Unauthorized",
        orders_before=0,
        orders_after=0,
        scenario="t",
    )
    assert [finding.invariant for finding in findings] == ["auth.error_body"]


def test_auth_control_must_succeed() -> None:
    assert check_auth_control(201, scenario="t") == []
    assert [f.invariant for f in check_auth_control(401, scenario="t")] == ["auth.control_valid"]
    assert [f.invariant for f in check_auth_control(None, scenario="t")] == ["auth.control_valid"]


def test_summary_counts() -> None:
    outcomes = [
        check_fuzz_response(
            case("amount_zero"),
            status=422,
            payload={"detail": []},
            raw_text="{}",
            orders_before=0,
            orders_after=0,
            scenario="t",
        )[0],
        check_fuzz_response(
            case("amount_zero"),
            status=500,
            payload=None,
            raw_text="",
            orders_before=0,
            orders_after=0,
            scenario="t",
        )[0],
    ]
    assert summary(outcomes) == {"total": 2, "ok": 1, "failed": 1}


@pytest.mark.parametrize("case_id", [item.case_id for item in ALL_CASES])
def test_no_case_is_structurally_empty(case_id: str) -> None:
    item = case(case_id)
    assert item.description
    assert item.path.startswith("/api/v1/")
    # A body-less DELETE is a complete request; everything else must carry a payload.
    if item.method in {"POST", "PATCH"}:
        assert item.body is not None or item.raw_body is not None
