# CURRENT TASK

**Objective:** a trader-complete paper stack, then a safe live mode. No placeholders, no
dead controls, no unverified claims.

**Working branch:** `integ/trader-superpower` (integration; `main` is the release branch).

## State (2026-10-23)

Merged into the integration release candidate (`integ/trader-superpower`):

- Multi-chain quote ladders (KyberSwap, Velora, LI.FI, Jupiter, Cetus, STON.fi, Osmosis
  SQS) with impact/fee/route truth and per-chain fail-closed ladders.
- Graph strategy core and HTTP API: catalogue, validation, dry run, backtests, scheduler
  wiring and per-strategy performance; visual builder live against the API.
- Paper read models: ledger, performance, attribution, limits, alerts and CSV exports.
- Every sidebar route implemented; the placeholder catch-all is gone; unknown routes 404.
- Account-scoped paper fill serialization, `POSITION_CHANGED`, quote deadlines and JSON
  validation errors; ADR-0027 and the full stress evidence are recorded.
- Full gate: 1044 Python + 94 Playwright; full `--mode all` stress PASS; live paper UI
  buy → position → full sell → reconciliation PASS; every live route PASS.

**Live executor:** `feat/live-executor` exists but its reviewer verdict is **BLOCKED**
(venue transactions not bound to the approved intent, sell caps can be bypassed, a
confirmed sell can be dropped from the live book). It is not merged, no live UI exists,
and `live_execute` remains false everywhere. Do not describe live mode as ready.

## Next, in order

1. Final reviewer PASS on the exact release SHA.
2. Re-run `bash tools/verify.sh --live` on a clean commit and record the commit/dirty flag.
3. Merge the integration release candidate to `main`; update the public readiness record.
4. Open a dedicated live-executor hardening slice; real-fund broadcast stays owner-only
   and unverified until a funded or forked-anvil pilot is recorded.
5. M2 data/ingest and the remaining visual/design work.

## Known limits

- One deployment bearer token; no per-user auth/RBAC.
- `/metrics` is unauthenticated; keep it off the public internet.
- Market coverage is 22/25; chains without DexScreener pairs stay `no_data`, never zero.
- Non-Solana paper fills use the chain quote ladder, or a capped `paper_mark` where no
  keyless provider exists.
- Per-token attribution exists; per-strategy attribution remains out of scope for a shared paper account.
- No live keys, signing, broadcast or live ledger.
- Maintainer-local notes are not part of this repository.
