//! Recorded cases from `Fill.bookable`, `ExecutionPipeline._submit` and the digest echo
//! check in `ExecutionPipeline._run` (`pipeline.py`, `tests/executor/test_transaction_binding.py`).

use ezchain_executor::binding::BoundAmount;
use ezchain_executor::receipt::{
    fill_is_bookable, verify_receipt, verify_receipt_bounds, verify_receipt_digest, FillStatus,
};

fn amount(text: &str) -> BoundAmount {
    BoundAmount::parse(text).expect("a valid decimal vector")
}

// --- Fill.bookable --------------------------------------------------------------

#[test]
fn bookable_matches_python_for_every_status_and_zero_case() {
    // Vectors produced from ezchain_core.execution.Fill.bookable.
    let one = amount("1");
    let ten = amount("10");
    let zero = amount("0");
    assert!(fill_is_bookable(FillStatus::Filled, &one, &ten, &ten));
    assert!(fill_is_bookable(FillStatus::Partial, &one, &ten, &ten));
    assert!(!fill_is_bookable(FillStatus::Rejected, &one, &ten, &ten));
    assert!(!fill_is_bookable(FillStatus::Filled, &zero, &ten, &ten));
    assert!(!fill_is_bookable(FillStatus::Filled, &one, &zero, &ten));
    assert!(!fill_is_bookable(FillStatus::Filled, &one, &ten, &zero));
}

// --- _submit checks -------------------------------------------------------------

#[test]
fn an_unbookable_receipt_is_refused_before_any_quantity_check() {
    let order_q = amount("10");
    assert_eq!(
        verify_receipt_bounds(
            FillStatus::Rejected,
            &amount("50"), // even if it also exceeds the order, bookability wins
            &amount("100"),
            &amount("2"),
            &order_q,
        ),
        Some("UNBOOKABLE_RECEIPT")
    );
}

#[test]
fn a_receipt_larger_than_the_order_is_refused() {
    assert_eq!(
        verify_receipt_bounds(
            FillStatus::Filled,
            &amount("10.01"),
            &amount("20"),
            &amount("2"),
            &amount("10"),
        ),
        Some("RECEIPT_EXCEEDS_ORDER")
    );
}

#[test]
fn a_receipt_within_the_order_is_accepted() {
    for quantity in ["10", "9.5", "0.00000001"] {
        assert_eq!(
            verify_receipt_bounds(
                FillStatus::Partial,
                &amount(quantity),
                &amount("20"),
                &amount("2"),
                &amount("10"),
            ),
            None,
            "quantity {quantity}"
        );
    }
}

#[test]
fn exact_decimal_comparison_does_not_round_a_tiny_excess() {
    // A fixed-precision decimal could round this into equality; the string comparison
    // cannot, so a receipt one minor unit over the order is still refused.
    assert_eq!(
        verify_receipt_bounds(
            FillStatus::Filled,
            &amount("1.00000001"),
            &amount("2"),
            &amount("2"),
            &amount("1"),
        ),
        Some("RECEIPT_EXCEEDS_ORDER")
    );
}

// --- digest echo check ----------------------------------------------------------

#[test]
fn an_echoed_digest_is_accepted() {
    let digest = "d".repeat(64);
    assert_eq!(verify_receipt_digest(Some(&digest), Some(&digest)), None);
}

#[test]
fn a_missing_or_swapped_receipt_digest_is_refused() {
    let digest = "d".repeat(64);
    assert_eq!(
        verify_receipt_digest(Some(&digest), None),
        Some("TRANSACTION_DIGEST_MISMATCH")
    );
    assert_eq!(
        verify_receipt_digest(Some(&digest), Some(&"0".repeat(64))),
        Some("TRANSACTION_DIGEST_MISMATCH")
    );
}

#[test]
fn an_unsigned_paper_run_has_no_digest_check() {
    // `if signed is not None and ...`: no signature means no echo requirement.
    assert_eq!(verify_receipt_digest(None, None), None);
    assert_eq!(
        verify_receipt_digest(None, Some("0".repeat(64).as_str())),
        None
    );
}

// --- combined order -------------------------------------------------------------

#[test]
fn verify_receipt_runs_bounds_before_the_digest_echo() {
    let digest = "d".repeat(64);
    assert_eq!(
        verify_receipt(
            Some(&digest),
            Some(&"0".repeat(64)),
            FillStatus::Filled,
            &amount("50"), // also exceeds the order
            &amount("100"),
            &amount("2"),
            &amount("10"),
        ),
        Some("RECEIPT_EXCEEDS_ORDER")
    );
}

#[test]
fn verify_receipt_accepts_a_fully_valid_receipt() {
    let digest = "d".repeat(64);
    assert_eq!(
        verify_receipt(
            Some(&digest),
            Some(&digest),
            FillStatus::Filled,
            &amount("10"),
            &amount("20"),
            &amount("2"),
            &amount("10"),
        ),
        None
    );
}
