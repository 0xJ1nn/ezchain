//! Recorded cases from `ezchain_core.signing` and `tests/executor/test_transaction_binding.py`.
//!
//! Decimal vectors were produced by running Python's `_canonical_decimal(Decimal(v))`
//! and `Decimal(a) > Decimal(b)` on the values below. The field-mismatch and signed-check
//! codes were produced by running `verify_prepared_transaction` / `verify_signed_transaction`
//! against the fake signing boundary. Python remains the oracle.

use std::cmp::Ordering;

use ezchain_executor::binding::{
    verify_prepared_transaction, verify_signed_transaction, ApprovedIntent, BoundAmount,
    DecimalError, PreparedTransaction, SignedTransaction, TransactionPayload,
};
use ezchain_executor::Side;

fn amount(text: &str) -> BoundAmount {
    BoundAmount::parse(text).expect("a valid decimal vector")
}

fn approved() -> ApprovedIntent {
    ApprovedIntent {
        chain: "solana".into(),
        cluster: "devnet".into(),
        mint: "Mint111".into(),
        side: Side::Buy,
        amount: amount("100"),
        min_out: amount("49.5"),
        recipient: "Wallet111".into(),
        program_id: "Program111".into(),
        idempotency_key: "intent-1".into(),
        digest: "a".repeat(64),
    }
}

fn payload() -> TransactionPayload {
    TransactionPayload {
        cluster: "devnet".into(),
        program_id: "Program111".into(),
        mint: "Mint111".into(),
        side: Side::Buy,
        amount: amount("100.0"),
        min_out: amount("49.50"),
        recipient: "Wallet111".into(),
        idempotency_key: "intent-1".into(),
    }
}

fn prepared() -> PreparedTransaction {
    PreparedTransaction {
        raw: b"the exact prepared bytes".to_vec(),
        payload_digest: "d".repeat(64),
        payload: payload(),
    }
}

fn signed_over(prepared: &PreparedTransaction) -> SignedTransaction {
    SignedTransaction {
        raw: prepared.raw.clone(),
        payload_digest: prepared.payload_digest.clone(),
        approval_digest: approved().digest,
    }
}

// --- _canonical_decimal vectors ------------------------------------------------

#[test]
fn canonical_spellings_match_python() {
    let vectors = [
        ("10", "10"),
        ("10.0", "10"),
        ("1.50", "1.5"),
        ("1E+3", "1000"),
        ("1e-7", "0.0000001"),
        ("-0.00", "0"),
        ("0", "0"),
        ("0.000", "0"),
        ("123.4500", "123.45"),
        ("-12.300", "-12.3"),
        ("100000000000000000000.5", "100000000000000000000.5"),
        ("0.1", "0.1"),
        ("2", "2"),
        ("9.9999999", "9.9999999"),
        ("+5", "5"),
        ("0005.0", "5"),
        ("0E+3", "0"),
        ("-0", "0"),
        ("1.0000000000000000000000001", "1.0000000000000000000000001"),
    ];
    for (input, expected) in vectors {
        assert_eq!(
            BoundAmount::parse(input).unwrap().as_canonical(),
            expected,
            "input {input}"
        );
    }
}

#[test]
fn non_finite_decimals_are_refused() {
    // Python: `_canonical_decimal(Decimal("NaN"))` raises ValueError.
    for input in ["NaN", "sNaN", "Infinity", "-Infinity", "+inf", "SNaN"] {
        assert_eq!(
            BoundAmount::parse(input).unwrap_err(),
            DecimalError::NonFinite,
            "input {input}"
        );
    }
}

#[test]
fn malformed_decimals_are_refused() {
    for input in ["", " ", "1.2.3", "1e", "1e+", "abc", "--1", "1_000"] {
        assert_ne!(BoundAmount::parse(input), Ok(amount("0")), "input {input}");
    }
}

#[test]
fn an_absurd_exponent_is_refused() {
    assert_eq!(
        BoundAmount::parse("1e999999"),
        Err(DecimalError::ExponentTooLarge)
    );
}

#[test]
fn numeric_comparison_matches_python_decimal() {
    let vectors = [
        ("10", "9.5", Ordering::Greater),
        ("0.1", "0.09999999999999999", Ordering::Greater),
        ("-1", "-2", Ordering::Greater),
        ("-2", "-1", Ordering::Less),
        ("1", "1.0", Ordering::Equal),
        ("0", "-0", Ordering::Equal),
        ("1e-7", "0", Ordering::Greater),
        (
            "100000000000000000000.5",
            "99999999999999999999.9",
            Ordering::Greater,
        ),
        ("1.5", "1.50", Ordering::Equal),
        ("0", "0.0001", Ordering::Less),
    ];
    for (left, right, expected) in vectors {
        assert_eq!(
            amount(left).numeric_cmp(&amount(right)),
            expected,
            "{left} vs {right}"
        );
    }
}

#[test]
fn is_positive_matches_decimal_greater_than_zero() {
    for (value, expected) in [
        ("1", true),
        ("0.00001", true),
        ("0", false),
        ("-0.0", false),
        ("-1", false),
    ] {
        assert_eq!(amount(value).is_positive(), expected, "value {value}");
    }
}

// --- verify_prepared_transaction -----------------------------------------------

#[test]
fn canonical_decimals_bind_alike() {
    // Python: amount 100.0 and 49.50 canonicalize to the approved 100 and 49.5.
    assert_eq!(verify_prepared_transaction(&prepared(), &approved()), None);
}

type FieldChange = Box<dyn Fn(&mut TransactionPayload)>;

#[test]
fn each_changed_field_reports_its_own_code() {
    let cases: [(&str, FieldChange); 8] = [
        (
            "TRANSACTION_PROGRAM_MISMATCH",
            Box::new(|p| p.program_id = "Foreign111".into()),
        ),
        (
            "TRANSACTION_CLUSTER_MISMATCH",
            Box::new(|p| p.cluster = "mainnet".into()),
        ),
        (
            "TRANSACTION_MINT_MISMATCH",
            Box::new(|p| p.mint = "Mint222".into()),
        ),
        (
            "TRANSACTION_SIDE_MISMATCH",
            Box::new(|p| p.side = Side::Sell),
        ),
        (
            "TRANSACTION_AMOUNT_MISMATCH",
            Box::new(|p| p.amount = amount("100.01")),
        ),
        (
            "TRANSACTION_MIN_OUT_MISMATCH",
            Box::new(|p| p.min_out = amount("49.4")),
        ),
        (
            "TRANSACTION_RECIPIENT_MISMATCH",
            Box::new(|p| p.recipient = "Wallet222".into()),
        ),
        (
            "TRANSACTION_IDEMPOTENCY_MISMATCH",
            Box::new(|p| p.idempotency_key = "intent-2".into()),
        ),
    ];
    for (expected, change) in cases {
        let mut p = payload();
        change(&mut p);
        let candidate = PreparedTransaction {
            payload: p,
            ..prepared()
        };
        assert_eq!(
            verify_prepared_transaction(&candidate, &approved()),
            Some(expected),
            "field {expected}"
        );
    }
}

#[test]
fn the_first_field_in_check_order_wins_on_multiple_changes() {
    let mut p = payload();
    p.program_id = "Foreign111".into();
    p.cluster = "mainnet".into();
    p.mint = "Mint222".into();
    let candidate = PreparedTransaction {
        payload: p,
        ..prepared()
    };
    assert_eq!(
        verify_prepared_transaction(&candidate, &approved()),
        Some("TRANSACTION_PROGRAM_MISMATCH")
    );
}

#[test]
fn an_amount_movement_in_either_direction_is_a_mismatch() {
    for value in ["99.99", "100.01", "101", "0"] {
        let mut p = payload();
        p.amount = amount(value);
        let candidate = PreparedTransaction {
            payload: p,
            ..prepared()
        };
        assert_eq!(
            verify_prepared_transaction(&candidate, &approved()),
            Some("TRANSACTION_AMOUNT_MISMATCH"),
            "amount {value}"
        );
    }
}

// --- verify_signed_transaction --------------------------------------------------

#[test]
fn a_matching_signature_is_accepted() {
    let prepared = prepared();
    assert_eq!(
        verify_signed_transaction(&signed_over(&prepared), &prepared, &approved()),
        None
    );
}

#[test]
fn a_foreign_approval_is_refused_first() {
    // ForeignApprovalSigner: signs verified bytes but attaches an approval nobody approved.
    let prepared = prepared();
    let mut signed = signed_over(&prepared);
    signed.approval_digest = "b".repeat(64);
    assert_eq!(
        verify_signed_transaction(&signed, &prepared, &approved()),
        Some("TRANSACTION_APPROVAL_MISMATCH")
    );
}

#[test]
fn bytes_that_were_not_the_decoded_bytes_are_refused() {
    // SwappedBytesSigner: a valid signer over different bytes, so the digest covers them.
    let prepared = prepared();
    let signed = SignedTransaction {
        raw: b"different prepared bytes".to_vec(),
        payload_digest: "e".repeat(64),
        approval_digest: approved().digest,
    };
    assert_eq!(
        verify_signed_transaction(&signed, &prepared, &approved()),
        Some("TRANSACTION_DIGEST_MISMATCH")
    );
}

#[test]
fn a_wrong_payload_digest_is_refused() {
    let prepared = prepared();
    let mut signed = signed_over(&prepared);
    signed.payload_digest = "0".repeat(64);
    assert_eq!(
        verify_signed_transaction(&signed, &prepared, &approved()),
        Some("TRANSACTION_DIGEST_MISMATCH")
    );
}

#[test]
fn a_bound_field_still_matters_when_the_digests_agree() {
    // verify_signed_transaction re-runs verify_prepared_transaction as defence in depth.
    let mut prepared = prepared();
    prepared.payload.mint = "Mint222".into();
    let signed = signed_over(&prepared);
    assert_eq!(
        verify_signed_transaction(&signed, &prepared, &approved()),
        Some("TRANSACTION_MINT_MISMATCH")
    );
}
