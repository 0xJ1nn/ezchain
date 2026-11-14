# LEDGER — rounding policy and bank-grade controls

The append-only ledger is the only accountant (CONTRACT I1). This document is normative
for every money value the ledger writes or compares, and it maps the bank-style controls
this change implements and the ones that remain open under GH-155.

## 1. Money and units

- The ledger's settlement asset is USD. Money is an **integer number of minor units** at
  scale 8: `MONEY_QUANTUM` = `0.00000001` USD (`MONEY_SCALE = 8`). This is finer than
  the fiat cent so sub-cent fees and long-tail notionals stay exact; "to the cent" is a
  floor on precision, not a licence to round early.
- A token quantity (`amount`) is an exact `Decimal` at the storage scale (18). It is a
  quantity, not money: it is validated finite and exact, but is not quantized to the USD
  scale.
- Storage is exact on both supported databases: `NUMERIC(38, 8)` on Postgres and an exact
  decimal string on SQLite. No column is a float, and no float crosses a read path.
- Serialization keeps money as a decimal string. A JSON number is never money.

## 2. Rounding policy

1. **Half-even, once.** A value that has to be rounded is rounded at the ledger boundary
   with `ROUND_HALF_EVEN` ("banker's rounding"), the SQL and IEEE-754 default. Never round
   inside a loop; round the value you are about to journal.
2. **Computed values are quantized.** The boundary function is
   `ezchain_core.money.quantize_money`. It is applied to a notional derived from
   `quantity * price`, a bps fee (`notional * fee_bps / 10_000`), a derived cost basis, a
   partial sell's derived proceeds, and the paper service's notional, fee and basis.
3. **Explicit values must be exact.** A value that is already on the quantum is written
   unchanged. A value with more than eight decimal places raises `SubQuantumError`; the
   ledger never absorbs, invents or silently rounds an explicit amount. Fail closed.
4. **Floats are refused.** A Python `float` at any money boundary raises
   `FloatMoneyError`. `Decimal(str(float))` is not an escape hatch: the exact value is
   already gone. Time budgets and ages may be floats; money may not.
5. **Comparisons are integer comparisons.** `to_minor_units()` converts an aligned
   `Decimal` to an integer. Balance checks sum integers and require exactly zero; no
   tolerance, no `abs(a - b) <= epsilon` in the ledger path.
6. **Presentation rounds only for display.** A formatted or rounded value is never fed
   back into the ledger.

| Case | Policy | Result |
|---|---|---|
| Buy notional from `quantity * price` | half-even, 8 dp | one journaled minor-unit amount |
| 30 bps fee on `0.0000003333` | half-even, 8 dp | `0.00000001` |
| Explicit `notional_usd=1.000000001` | fail closed | `SubQuantumError` |
| `usd_value=0.1` (float) | fail closed | `FloatMoneyError` |
| Group sums to `0.00000001` | fail closed | `LedgerError` |
| Group sums to `0` | accepted | exact double entry |

## 3. Controls

Implemented by this change:

| Control | Evidence |
|---|---|
| Double-entry completeness | `assert_balanced` requires an exact zero minor-unit sum; the buy/sell/genesis factories assert it before returning. `tests/paper/test_ledger_hardening.py`, `tests/paper/test_accounting.py`. |
| Exact minor-unit accounting | `packages/core-py/src/ezchain_core/money.py`; zero-drift property test across eight seeded random walk tapes. |
| Reconciliation | `apps/api/src/ezchain_api/services/reconciliation.py` re-derives every journal group, fill, position and cash figure from persisted rows. `tests/paper/test_reconciliation.py`, `tests/paper/test_property.py`. |
| Immutability evidence | `ledger_digest` / `seal_entries` / `verify_seal`: a chained SHA-256 over the ordered tape; any changed entry changes the head. `tests/paper/test_ledger_hardening.py`. |
| Replay determinism | `replay_balances` projects one ordered tape to canonical, account-sorted amounts and USD minor units; the same tape always produces the same balances and seal. |

Open under GH-155 (not implemented here):

- **Period-locked entries** — a close/lock record plus a write guard that refuses a
  journal leg dated inside a locked period. Needs a schema addition and an operator
  action; not silently inferred from timestamps.
- **On-chain verification service** — asynchronous reconciliation of every tracked
  balance and position against the chain, with drift alerts and quarantine until the
  drift is explained. The read-side reconciliation exists; the chain-side poller, alert
  state and quarantine flag do not.
- **Forensic audit command** — a command that re-verifies an arbitrary period end to
  end. The pure-core primitives it needs (ordered replay, exact balance, seal,
  reconciliation) now exist; the command, its period selection and its report do not.

The seal is currently computed over a tape supplied by the caller, not persisted by the
database. Persisting the head (and anchoring it outside the database) is the next
immutability step; until then the seal is tamper-evidence within one process or export.

## 4. Using the seal and replay

```python
from ezchain_core.ledger import replay_balances, seal_entries, verify_seal

seal = seal_entries(tape)          # LedgerSeal(count=..., head=...)
verify_seal(tape, seal)            # raises LedgerTamperedError on any difference
for row in replay_balances(tape):  # canonical, account-sorted
    print(row.account, row.amount, row.usd_minor)
```

A forensic export is the ordered tape plus its seal. Re-verifying the export re-runs
`verify_seal` and the reconciliation report; a mismatch is evidence, never something the
reader repairs in place.

## 5. Verification

```bash
env -u PYTHONPATH .venv/bin/pytest tests/paper -q -k "ledger or rounding or drift or minor"
```

`tests/paper/test_ledger_hardening.py` proves the policy on the arithmetic itself:
floats and sub-minor values fail closed, a one-minor-unit imbalance is rejected, the
same tape produces the same seal and replay, a changed entry is detected, and eight
seeded 250-operation random walks conserve every minor unit with zero drift.

Any change to a money formula needs an exact-conservation test and an entry in
`docs/DECISIONS.md` when it moves a boundary.
