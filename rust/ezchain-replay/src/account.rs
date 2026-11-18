//! Account naming rules, copied verbatim from `ledger.py`.
//!
//! These are the only spellings the projection recognises. `position_account`
//! is deliberately a plain concatenation: the chain and token address are
//! opaque to the ledger, which never branches on chain identity (CONTRACT I6).

/// The settlement cash account (`cash_account()` in Python).
pub const CASH_ACCOUNT: &str = "cash:USD";
/// The fee expense account (`fee_account()` in Python).
pub const FEE_ACCOUNT: &str = "fees:USD";
/// The realized-PnL contra account (`realized_account()` in Python).
pub const REALIZED_ACCOUNT: &str = "pnl:realized:USD";
/// The contra account credited by a no-risk paper top-up. It is capital, not
/// equity or PnL, so a top-up stays distinguishable from trading results.
pub const FUNDING_ACCOUNT: &str = "funding:USD";
/// Prefix used for funding group ids in the API reconciliation read model.
pub const FUNDING_PREFIX: &str = "funding:";

#[must_use]
pub fn cash_account() -> &'static str {
    CASH_ACCOUNT
}

#[must_use]
pub fn fee_account() -> &'static str {
    FEE_ACCOUNT
}

#[must_use]
pub fn realized_account() -> &'static str {
    REALIZED_ACCOUNT
}

/// `f"position:{chain}:{token_address}"` from Python, byte for byte.
#[must_use]
pub fn position_account(chain: &str, token_address: &str) -> String {
    format!("position:{chain}:{token_address}")
}

/// True when an account belongs to the funding contra family. Mirrors the
/// `FUNDING_PREFIX` check the Python read models use to classify funding rows.
#[must_use]
pub fn is_funding(account: &str) -> bool {
    account.starts_with(FUNDING_PREFIX)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn account_names_match_python_literals() {
        assert_eq!(cash_account(), "cash:USD");
        assert_eq!(fee_account(), "fees:USD");
        assert_eq!(realized_account(), "pnl:realized:USD");
        assert_eq!(FUNDING_ACCOUNT, "funding:USD");
        assert_eq!(FUNDING_PREFIX, "funding:");
        assert_eq!(
            position_account("solana", "So11111111111111111111111111111111111111112"),
            "position:solana:So11111111111111111111111111111111111111112"
        );
        assert!(is_funding("funding:order-1"));
        assert!(!is_funding("cash:USD"));
    }
}
