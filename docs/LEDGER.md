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
  quantity, not money: it is validated finite and exact, and it is never compared as if it
  were USD.
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
   partial sell's derived proceeds, the paper service's notional, fee and basis, and the
   cost preview's `worst_case_out`.
3. **Precision is derived, never ambient.** Every conversion and computation here sizes a
   `localcontext` from its operands' digit count (`exact_precision`) and sums with
   `exact_sum`. Python's ambient context is 28 significant digits, and a notional may carry
   more: `to_minor_units`/`quantize_money`/`bps_fee` under that default silently round, and
   a balance check built on them would accept a group that is a whole minor unit out. A
   value that does not fit the derived precision **fails closed** (`SubQuantumError`);
   nothing is rounded "because it did not fit".
4. **Explicit values must be exact.** A value that is already on the quantum is written
   unchanged. A value with more than eight decimal places raises `SubQuantumError`; the
   ledger never absorbs, invents or silently rounds an explicit amount. Fail closed.
5. **Floats are refused.** A Python `float` at any money boundary raises
   `FloatMoneyError`. `Decimal(str(float))` is not an escape hatch: the exact value is
   already gone. Time budgets and ages may be floats; money may not.
6. **Comparisons are exact integer comparisons.** `to_minor_units()` converts an aligned
   `Decimal` to an integer; balance checks and the reconciliation read model sum integers
   and require exactly zero. There is no tolerance and no `abs(a - b) <= epsilon` anywhere
   in the ledger path: a persisted row that is not a whole number of minor units is
   *reported*, never quietly compared at a coarser scale. Token *quantities* are compared
   with a 1e-8 dust bound because they are 18-decimal quantities, not money; that bound is
   named `QUANTITY_TOLERANCE` and it is never applied to a USD figure.
7. **Presentation rounds only for display.** A formatted or rounded value is never fed
   back into the ledger.

| Case | Policy | Result |
|---|---|---|
| Buy notional from `quantity * price` | half-even, 8 dp | one journaled minor-unit amount |
| 30 bps fee on `0.0000033333` (exactly `9.9999e-9`) | half-even, 8 dp | `0.00000001` |
| 30 bps fee on `0.0000003333` (exactly `9.999e-10`) | half-even, 8 dp | `0` |
| `to_minor_units` of `1234567890123456789012.00000001`, ambient precision 28 | derived precision, exact | `123456789012345678901200000001` minor units, no rounding |
| Explicit `notional_usd=1.000000001` | fail closed | `SubQuantumError` |
| `usd_value=0.1` (float) | fail closed | `FloatMoneyError` |
| Group sums to `0.00000001` | fail closed | `LedgerError` |
| Group sums to `0` | accepted | exact double entry |

### 2.1 This change moves the fee quantum from 1e-6 to 1e-8

The bps fee used to be quantized to `0.000001`; it is now quantized to `MONEY_QUANTUM`
(`0.00000001`). **Fees that were previously journaled correctly under the old rule change
under the new one, and not only dust:** the old rule discarded every digit past the sixth;
the new rule keeps them down to the ledger's own unit, so any fee whose exact
`notional * fee_bps / 10_000` has more than six decimals moves. A fee that is already a
whole micro-dollar is unchanged.

| Notional | Fee bps | Exact fee | Old journaled (1e-6) | New journaled (1e-8) |
|---|---|---|---|---|
| `0.0000033333` | 30 | `0.0000000099999` | `0.000000` | `0.00000001` |
| `0.00000101` | 50 | `0.00000000505` | `0.000000` | `0.00000001` |
| `0.000412` | 30 | `0.000001236` | `0.000001` | `0.00000124` |
| `1234.5678` | 30 | `3.7037034` | `3.703703` | `3.70370340` |
| `100` | 30 | `0.3` | `0.300000` | `0.30000000` (same value) |
| `0.0000003333` | 30 | `0.0000000009999` | `0.000000` | `0` (same value) |

No published row is rewritten: the ledger is append-only, so a fee journaled under the old
rule keeps its old value and its group still balances exactly. Nothing recomputes a
historical fee at the new scale — a read model compares a fee to the journal that recorded
it — so an old row is reported only when it disagrees with its own legs, never for being a
micro-dollar coarse.

## 3. Accounts and the journal paths that exist

- `cash:USD` — settlement cash. Its balance is the sum of its legs; no scalar caches it.
- `position:{chain}:{token}` — a held token quantity and its USD cost basis.
- `fees:USD` — fees paid, expensed from cash.
- `pnl:realized:USD` — gross realized PnL on sells (the fee is its own leg).
- `equity:genesis` — the single starting-capital contra leg.
- `funding:USD` — the contra leg of a no-risk paper top-up (`funding_entries`, group
  `funding:{order.id}`). It is neither equity nor PnL: funded capital stays
  distinguishable from trading results.

The ledger has exactly these credit paths: **genesis** once per deployment, and
**funding** top-ups. **There is no withdrawal or transfer journal in this slice.** Paper
cash leaves the book only by being spent on a fill's notional or fee; nothing credits it
back out to an external account. The mixed-tape property says this explicitly instead of
implying an exit path that does not exist.

## 4. Controls

Implemented by this change:

| Control | Evidence |
|---|---|
| Double-entry completeness | `assert_balanced` requires an exact zero minor-unit sum; the buy/sell/genesis/funding factories assert it before returning. `tests/paper/test_ledger_hardening.py`, `tests/paper/test_accounting.py`. |
| Exact minor-unit accounting | `packages/core-py/src/ezchain_core/money.py` (`exact_precision`, `exact_sum`, `to_minor_units`); zero-drift property test over eight seeded 250-operation **mixed** tapes (funding, explicit and derived notionals, full exits, derived partial sells). |
| Reconciliation | `apps/api/src/ezchain_api/services/reconciliation.py` re-derives every journal group, fill, position and cash figure from persisted rows and compares money as exact integer minor units. `tests/paper/test_reconciliation.py`, `tests/paper/test_property.py`. |
| Immutability evidence | `ledger_digest` / `seal_entries` / `verify_seal`: a chained SHA-256 over the ordered tape. Each link is canonical JSON (`ezchain.ledger/v2`) covering the index, account, asset, amount, USD value, kind, `group_id`, `created_at`, `wallet_id`, the agent bucket (`bucket_id`) and the actor (`actor_kind`, `actor_id`). Any changed field changes the head, and no two distinct entries can serialize to the same payload. `tests/paper/test_ledger_hardening.py`. |
| Replay determinism | `replay_balances` / `replay_position` project one ordered tape to canonical, account-sorted amounts and USD minor units, summing with `exact_sum`; the same tape always produces the same balances and seal. |

The seal commits to `group_id`, `created_at`, `wallet_id`, `bucket_id` and the actor, so a
tape whose attribution — including which agent bucket a cash leg charged — was edited is a
different tape. `entry_from_row` projects a persisted
`ledger_entries` row into the sealed entry, which is what a forensic export re-verifies.

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
- **Withdrawal / transfer journal** — no path exists, so there is nothing to harden yet.
  When one is added it needs its own contra account and the same exactness.

The seal is currently computed over a tape supplied by the caller, not persisted by the
database. Persisting the head (and anchoring it outside the database) is the next
immutability step; until then the seal is tamper-evidence within one process or export.

## 5. Using the seal and replay

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

## 6. Verification

```bash
env -u PYTHONPATH .venv/bin/pytest tests/paper -q -k "ledger or rounding or drift or minor"
```

`tests/paper/test_ledger_hardening.py` proves the policy on the arithmetic itself:
floats and sub-minor values fail closed, a one-minor-unit imbalance at a 22-integer-digit
notional is rejected, the v1 `"|"` payload is forged to produce one head for two different
entries and the shipped seal is shown to split them, a tape whose group/time/wallet/actor
changed is a different head, the same tape produces the same seal and replay, a changed
entry is detected, the §2.1 fee table is pinned row by row against both quanta, and eight
seeded 250-operation mixed tapes (funding top-ups, explicit and derived notionals, full
exits, derived partial sells) conserve every minor unit with zero drift.

Any change to a money formula needs an exact-conservation test and an entry in
`docs/DECISIONS.md` when it moves a boundary.
