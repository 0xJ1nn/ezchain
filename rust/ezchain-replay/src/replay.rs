//! The pure replay projection, ported line for line from `ledger.py`.
//!
//! Every function folds the tape the caller supplies; none writes state. The
//! tape must be in journal order (oldest first) for [`replay_position`], whose
//! Python original notes that cost basis is removed at the then-current
//! average. [`replay_balances`] is canonical and account-sorted regardless of
//! tape order, matching Python's `sorted(amounts)`.

use std::cmp::Ordering;
use std::collections::BTreeMap;

use ezchain_decimal::{
    compare, exact_sum, to_minor_units, Context, Decimal, MinorUnits, DEFAULT_CONTEXT,
};

use crate::account;
use crate::entry::LedgerEntry;
use crate::error::ReplayError;
use crate::minor::{add as add_minor, zero as zero_minor};

/// One account's replay: exact token amount and exact USD minor units.
/// Mirrors the Python `AccountBalance` dataclass.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AccountBalance {
    pub account: String,
    pub amount: Decimal,
    pub usd_minor: MinorUnits,
}

/// Project every account's amount and USD minor units from one ordered tape.
///
/// Amounts are summed exactly via `ezchain_decimal::exact_sum` at a precision
/// derived from the operands, so neither the ambient context nor the tape
/// length can leave a residue. USD is summed as arbitrary-precision integers,
/// so there is nothing to drift. The result is sorted by account, byte for
/// byte as Python's `sorted` over `str` keys.
pub fn replay_balances(entries: &[LedgerEntry]) -> Result<Vec<AccountBalance>, ReplayError> {
    let mut amounts: BTreeMap<String, Vec<Decimal>> = BTreeMap::new();
    let mut usd: BTreeMap<String, MinorUnits> = BTreeMap::new();

    for entry in entries {
        amounts
            .entry(entry.account.clone())
            .or_default()
            .push(entry.amount.clone());
        let minor = to_minor_units(&entry.usd_value).map_err(ReplayError::from)?;
        let total = usd.entry(entry.account.clone()).or_insert_with(zero_minor);
        *total = add_minor(total, &minor);
    }

    let mut balances = Vec::with_capacity(amounts.len());
    for (account_name, values) in amounts {
        let amount = exact_sum(&values).map_err(ReplayError::from)?;
        let usd_minor = usd.get(&account_name).cloned().unwrap_or_else(zero_minor);
        balances.push(AccountBalance {
            account: account_name,
            amount,
            usd_minor,
        });
    }
    Ok(balances)
}

/// Every group must sum to exactly zero minor units. No tolerance is
/// permitted. The caller passes one journal group at a time, as the Python
/// constructors (`genesis_entries`, `buy_entries`, `sell_entries`,
/// `funding_entries`) do; a concatenation of whole balanced groups also sums
/// to zero and is therefore accepted, exactly as in Python.
pub fn assert_balanced(entries: &[LedgerEntry]) -> Result<(), ReplayError> {
    let mut total = zero_minor();
    for entry in entries {
        let minor = to_minor_units(&entry.usd_value).map_err(ReplayError::from)?;
        total = add_minor(&total, &minor);
    }
    if !total.magnitude.is_zero() {
        return Err(ReplayError::Unbalanced { total_minor: total });
    }
    Ok(())
}

/// Return `(quantity, cost_basis_usd)` for one position account's tape.
///
/// The caller hands over the entries of a single position account in ledger
/// order. A zero quantity zeroes the cost, matching Python: a closed position
/// reports no residual basis, even if the replayed legs would leave one.
pub fn replay_position(entries: &[LedgerEntry]) -> Result<(Decimal, Decimal), ReplayError> {
    let quantities: Vec<Decimal> = entries.iter().map(|entry| entry.amount.clone()).collect();
    let costs: Vec<Decimal> = entries
        .iter()
        .map(|entry| entry.usd_value.clone())
        .collect();

    let quantity = exact_sum(&quantities).map_err(ReplayError::from)?;
    let mut cost = exact_sum(&costs).map_err(ReplayError::from)?;

    if compare(&quantity, &Decimal::from_i64(0)) == Ordering::Less {
        return Err(ReplayError::NegativeQuantity);
    }
    if quantity.is_zero() {
        cost = Decimal::from_i64(0);
    }
    Ok((quantity, cost))
}

/// Gross realized PnL: the negated replay of the realized-PnL contra account.
///
/// Python writes `-exact_sum(...)`, which is the *ambient-context* unary
/// minus, not `copy_negate`: it rounds to the context precision (28 digits)
/// and turns a signed zero into positive zero. Mirror both behaviours exactly.
pub fn realized_pnl(entries: &[LedgerEntry]) -> Result<Decimal, ReplayError> {
    let values: Vec<Decimal> = entries
        .iter()
        .filter(|entry| entry.account == account::realized_account())
        .map(|entry| entry.usd_value.clone())
        .collect();
    let total = exact_sum(&values).map_err(ReplayError::from)?;
    if total.is_zero() {
        let mut positive_zero = total;
        positive_zero.sign = false;
        return Ok(positive_zero);
    }
    let ambient = Context::with_precision(DEFAULT_CONTEXT.prec);
    total
        .mul(&Decimal::from_i64(-1), &ambient)
        .map_err(ReplayError::from)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(account: &str, amount: &str, usd: &str) -> LedgerEntry {
        LedgerEntry::new(account, "USD", amount, usd, "journal").unwrap()
    }

    #[test]
    fn balances_are_exact_and_account_sorted() {
        let tape = vec![
            entry("cash:USD", "1.5", "2.00000000"),
            entry("position:sol:TOK", "3", "2.00000000"),
            entry("cash:USD", "-1.5", "-2.00000000"),
        ];
        let balances = replay_balances(&tape).unwrap();
        let accounts: Vec<&str> = balances.iter().map(|b| b.account.as_str()).collect();
        assert_eq!(accounts, vec!["cash:USD", "position:sol:TOK"]);
        assert_eq!(crate::minor::signed_string(&balances[0].usd_minor), "0");
        assert_eq!(
            crate::minor::signed_string(&balances[1].usd_minor),
            "200000000"
        );
        assert_eq!(balances[0].amount.str_repr(), "0.0");
    }

    #[test]
    fn conservation_is_tolerance_free() {
        let balanced = vec![
            entry("cash:USD", "1", "1.00000000"),
            entry("fees:USD", "1", "-1.00000000"),
        ];
        assert!(assert_balanced(&balanced).is_ok());

        let mut unbalanced = balanced;
        unbalanced[1] = entry("fees:USD", "1", "-0.99999999");
        let err = assert_balanced(&unbalanced).unwrap_err();
        assert_eq!(err.code(), "LedgerError");
        assert_eq!(
            err.message(),
            "ledger group does not balance: 1 minor units"
        );
    }

    #[test]
    fn a_closed_position_has_no_residual_cost() {
        let tape = vec![
            entry("position:sol:TOK", "2", "20.00000000"),
            entry("position:sol:TOK", "-2", "-20.00000000"),
        ];
        let (quantity, cost) = replay_position(&tape).unwrap();
        assert!(quantity.is_zero());
        assert_eq!(cost.str_repr(), "0");
    }

    #[test]
    fn a_negative_position_quantity_is_refused() {
        let tape = vec![entry("position:sol:TOK", "-1", "1.00000000")];
        let err = replay_position(&tape).unwrap_err();
        assert_eq!(err.code(), "ValueError");
        assert_eq!(err.message(), "position quantity cannot be negative");
    }

    #[test]
    fn realized_pnl_negates_the_contra_account() {
        let tape = vec![
            entry("cash:USD", "1", "5.00000000"),
            entry("pnl:realized:USD", "1", "-5.00000000"),
        ];
        assert_eq!(realized_pnl(&tape).unwrap().str_repr(), "5.00000000");
    }
}
