//! Receipt verification, ported from the pure checks in `pipeline.py`.
//!
//! `_submit` refuses an unbookable receipt (`UNBOOKABLE_RECEIPT`) and a receipt larger
//! than the order (`RECEIPT_EXCEEDS_ORDER`). After `_submit` returns, `_run` refuses a
//! receipt whose digest does not echo the signed payload (`TRANSACTION_DIGEST_MISMATCH`).
//! Nothing here computes a digest, moves money or writes a ledger: the digests and the
//! receipt fields arrive already measured from the Python boundary.

use std::cmp::Ordering;

use crate::binding::BoundAmount;

/// The receipt status that drives `Fill.bookable`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FillStatus {
    Filled,
    Partial,
    Rejected,
}

/// Mirrors `Fill.bookable` (`ezchain_core.execution`): accepted, sized and priced.
pub fn fill_is_bookable(
    status: FillStatus,
    quantity: &BoundAmount,
    notional_usd: &BoundAmount,
    price_usd: &BoundAmount,
) -> bool {
    status != FillStatus::Rejected
        && quantity.is_positive()
        && notional_usd.is_positive()
        && price_usd.is_positive()
}

/// The two `_submit` checks, in Python's order. `None` when the receipt may be booked.
pub fn verify_receipt_bounds(
    status: FillStatus,
    fill_quantity: &BoundAmount,
    fill_notional_usd: &BoundAmount,
    fill_price_usd: &BoundAmount,
    order_quantity: &BoundAmount,
) -> Option<&'static str> {
    if !fill_is_bookable(status, fill_quantity, fill_notional_usd, fill_price_usd) {
        return Some("UNBOOKABLE_RECEIPT");
    }
    if fill_quantity.numeric_cmp(order_quantity) == Ordering::Greater {
        return Some("RECEIPT_EXCEEDS_ORDER");
    }
    None
}

/// Mirrors the digest echo check in `ExecutionPipeline._run`:
/// `if signed is not None and fill.transaction_digest != signed.payload_digest`.
///
/// `None` for `signed_payload_digest` models an unsigned paper run, where no signature
/// was produced and therefore no digest can be echoed. A signed run whose receipt has a
/// missing or different digest is refused — the receipt is not for our transaction.
pub fn verify_receipt_digest(
    signed_payload_digest: Option<&str>,
    receipt_digest: Option<&str>,
) -> Option<&'static str> {
    match signed_payload_digest {
        None => None,
        Some(expected) if receipt_digest == Some(expected) => None,
        Some(_) => Some("TRANSACTION_DIGEST_MISMATCH"),
    }
}

/// Both checks in pipeline order: `_submit` bounds first, then the `_run` digest echo.
pub fn verify_receipt(
    signed_payload_digest: Option<&str>,
    receipt_digest: Option<&str>,
    status: FillStatus,
    fill_quantity: &BoundAmount,
    fill_notional_usd: &BoundAmount,
    fill_price_usd: &BoundAmount,
    order_quantity: &BoundAmount,
) -> Option<&'static str> {
    verify_receipt_bounds(
        status,
        fill_quantity,
        fill_notional_usd,
        fill_price_usd,
        order_quantity,
    )
    .or_else(|| verify_receipt_digest(signed_payload_digest, receipt_digest))
}
