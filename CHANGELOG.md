# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). SemVer.

## [Unreleased]

### Added

- **A gap in the pump.fun firehose is now visible and untradeable (ingest).** The
  firehose adapter's entry read, `FirehoseFeed.entries(now)`, raises `StaleFeed` once the
  newest observation is older than the 60 s budget, and the refusal carries the age, the
  budget and the last event's timestamp and drains nothing. Once the feed is live again,
  every drained observation is still aged on its own `observed_at`, and any item older than
  the budget is refused and counted in `refused_stale` — so one fresh event after a gap
  cannot revive the backlog that accrued while the feed was down, and an entry is only ever
  built from an observation the stream can defend. `FirehoseFeed.health(now)` reports the
  facts an operator needs from one measured snapshot: `last_event_age_seconds` (and
  `last_event_at`), `reconnects`, `dropped`, `refused_stale`, and
  `decoded_ratio`/`rejected_ratio`; an unobserved feed reports `null` for age and both
  ratios rather than a fresh-looking zero or a perfect `1.0`. A stale transition is
  recorded by `ingest()` as well as by a reader, so an outage nobody happened to read still
  leaves exactly one `provider_stale` alert. The handoff to a consumer is a bounded buffer
  (`FirehoseFeed(queue_size=…)`, default 500) that drops the oldest observation under
  pressure and counts every drop, and whose census is synchronous so observing it never
  blocks the socket reader. `FirehoseSource.receive` now decodes before it observes the
  gate, so only a decoded event proves the feed is alive and a rejected payload cannot
  refresh it; `pull()` applies the same per-item age rule and counts its refusals in
  `refused_stale`. The gate and queue are injected through protocols, so that entry rule is
  tested without the optional package installed. `FirehoseSource.health()` gains `dropped`,
  `refused_stale`, `decoded_ratio` and `rejected_ratio` over the package's own gate and
  queue. `entries()`/`pull()` are the only entry reads: `snapshot()`, `fresh_events()` and
  `stream()` are display surfaces whose rows carry a read-time `age_seconds` and `stale`
  flag, and a `stream()` delta is stamped with the clock it is read at.
  `tests/adapters/fixtures/firehose_sample.json` now carries one record of real pump.fun
  traffic plus five synthetic records behind a provenance header, with the signature, mint,
  user and creator of every record replaced by sentinels, replaying through the same
  `normalize_firehose_event` path every live event uses. See
  [docs/INFRA.md §6](docs/INFRA.md#6-health-metrics-and-alerts).
- **Prompt-injection tests that assume the model is compromised.** A corpus of hostile
  inputs — instruction overrides, a pasted "operator: approved" chat, a token name carrying
  a fake system message, forged approval JSON in a tool result, a strategy description
  asking for the key, unicode/homoglyph obfuscation, a padding attack, a request to switch
  the session to a live venue and a request to read every agent's bucket — each named by
  attack class, is driven through the **real** loop, the real MCP server and the real API
  with a scripted model that obeys the injection. The suite asserts the structure holds
  anyway: no fill row, every agent order still `pending_approval`, no call needing `admin`,
  no budget overrun, no bucket beyond the caller's own and no agent or deployment key in a
  prompt, answer or observation. It lives in `tests/agent_runtime/`, which `pytest -q` in
  `tools/ci.sh` already collects; no network and no sleeps, and a guard removed by hand
  makes it fail. The matrix and what is **not** covered are
  [docs/PROMPT-INJECTION.md](docs/PROMPT-INJECTION.md).
- **A headless command for the agent runtime.** `ezchain-harness` runs one session on the
  real `AgentLoop` — the same budgets, kill switch and approval path the panel will use —
  and prints each step to stdout as it happens: the session id, the MCP tool surface, each
  turn, each tool call with its one-line result summary, the sourced answer, and a final
  `end` line with the typed `StopReason`, the state and the spend. The model and the MCP
  toolbox are `module:attribute` factories, so the command carries no provider SDK of its
  own, and a factory that cannot start is reported as a usage error rather than a failed
  session. Exit codes are `0` completed, `1` failed, `2` usage, `3` stopped (kill switch or
  budget) and `4` awaiting approval; a session waiting for a person, stopped or failed
  never exits `0`. The suite scripts both seams, so it makes no network call and needs no
  provider key. See [docs/AGENT-INTEGRATION.md §9.1](docs/AGENT-INTEGRATION.md#91-the-headless-command).

- **A Compose drill that proves the stack at the current migration head (PAPER).**
  `tools/ops/drill.sh` (wrapping `python -m tools.ops drill`) starts
  `infra/compose.yaml` under an isolated project, then requires `/readyz` to report
  `migration: head` at the revision in this checkout, the one-shot `migrate` container to
  exit `0`, one paper buy through the edge to fill and replay to the same order id with a
  reconciliation of `ok: true`, cash and positions to be unchanged across an API restart,
  and a dump restored into a scratch database to reproduce the ledger row count. It tears
  the project down afterwards, including on failure. Exit codes: `0` passed, `1` a phase
  failed, `2` refused to start — no Docker CLI or daemon, an `infra/.env` without real
  secrets, or a rejected `compose config`. A refusal is never a pass, so a host without a
  daemon gets `2` from the default invocation; `--static-only` validates the compose
  structure and the Alembic chain (one head, no branch point) without one. The gate takes
  it only on request (`EZCHAIN_GATE_DRILL=static|run`, default `off`). Recorded at head
  `0022_strategy_creator`: the daemon-free phase passed, the Compose phases were skipped
  for the missing daemon and the application phases were replayed on a host Postgres —
  see [docs/evidence/compose-drill/README.md](docs/evidence/compose-drill/README.md) and
  [RUNBOOK §12.9](docs/RUNBOOK.md#129-compose-drill).

- **An agent spends from a capped ledger bucket (PAPER).** An agent key's orders now draw
  only from the cash its owner set aside for that agent, and the cap is **per agent**
  (keyed on the key id), not per workspace. The bucket is not a balance: `agent_buckets`
  holds the cap and `ledger_entries.bucket_id` attributes cash legs, so what an agent has
  deployed is `-(sum of attributed cash legs)` replayed from the journal. An order that
  would take the deployment past the cap is refused `AGENT_BUCKET_EXCEEDED` **before the
  market read and the quote**, with the cap, deployment, available room and worst case in
  dollars on the reason. The gate is entry-only: lowering a cap below what is deployed
  stops new buys and never refuses a sell, because a cap may not trap a position — the exit
  returns cash and brings the deployment back inside it. The fill fee counts as deployment.
  An agent-created strategy resolves to its creator's bucket, so wrapping automation in a
  strategy does not escape the cap; a person's order draws from no bucket. A cash leg is
  attributed to the bucket that **owns the position it settles**, never to whoever placed
  the order: a person closing an agent's position returns the agent's cash, and an agent
  selling a position no bucket owns is credited nothing. An agent with no
  cap has no bucket limit, which is the unchanged behaviour. Only a person, and only the
  agent's own owner, may set a cap — an agent key is `403 AGENT_BUCKET_REQUIRES_PERSON` —
  and an unreadable bucket state refuses `AGENT_BUCKET_UNREADABLE` rather than reading as
  unlimited. Approval is not a bypass: approving re-enters `place_order`, so the bucket gate
  runs again. New routes: `GET /api/v1/agent/buckets`, `GET /api/v1/agent/buckets/{agent_id}`
  and `PUT /api/v1/agent/buckets/{agent_id}`. Migration `0024_agent_buckets` is additive,
  sitting on `0022_strategy_creator`; nothing is backfilled.
  agent's own owner, may set a cap — an agent key is `403 AGENT_BUCKET_REQUIRES_PERSON`,
  and a cap for an id with no minted key is `404 AGENT_BUCKET_UNKNOWN`, so nobody can
  pre-set one — and an unreadable bucket state refuses `AGENT_BUCKET_UNREADABLE` rather than
  reading as unlimited. Approval is not a bypass: approving re-enters `place_order`, so the
  bucket gate runs again. New routes: `GET /api/v1/agent/buckets`,
  `GET /api/v1/agent/buckets/{agent_id}` and `PUT /api/v1/agent/buckets/{agent_id}`.
  Migration `0023_agent_buckets` is additive, sitting on `0022_strategy_creator`; nothing
  is backfilled, and `cap_usd` keeps the exact-decimal type on both backends (`NUMERIC` on
  Postgres, `TEXT` on SQLite, whose `NUMERIC` is a lossy `REAL`). Recorded as ADR-0044.
- **A tested backup: scripted, idempotent, and restored with a measured recovery time
  (PAPER).** `python -m tools.ops` adds `backup`, `restore`, `verify`, `rehearse` and
  `drop`. A backup writes the `pg_dump` and a manifest that records the dump's SHA-256 plus
  a row digest of every table, read from the **same exported snapshot**; a second run on an
  unchanged source reuses the verified dump instead of writing a second copy, and a changed
  source always gets a new one. `restore` rebuilds a freshly created database (its name must
  contain `test`, `scratch` or `_ci`, the same rule `tools/check-migrations.sh` applies),
  then requires every ledger table to match the manifest row-for-row and the API's own
  reconciliation report to be `ok: true`; a short restore fails with the table and the
  difference named and exits `1`. `rehearse` runs the whole incident — build a source
  database through the real paper order path, back it up, **drop it**, restore into a new
  database, verify — and records the elapsed recovery time. The first measured drill on
  Postgres 16 restored 33 ledger entries and 7 fills in 0.54–7.44 s across five idle runs and is
  recorded in [docs/evidence/release-gate/backup-restore-rehearsal.md](docs/evidence/release-gate/backup-restore-rehearsal.md).
  [RUNBOOK §8](docs/RUNBOOK.md#8-restore-drill--disaster-recovery) is the procedure, with a
  table for what to do when a restore comes back short; [INFRA §7](docs/INFRA.md#7-storage-backup-and-restore)
  is the deployment contract. Docker Compose was not exercised (no daemon on this host).
- **Trade policy has one surface, and a strategy may carry its own profile (PAPER).**
  `GET /api/v1/trade-policy` serves the five sizing fields the order gate reads
  (`min_order_usd`, `max_order_usd`, `max_order_pct_of_equity`, `per_token_cap_usd`,
  `max_slippage_bps`) with each value, range and description, the three `low`/`mid`/`high`
  profiles, the active profile, and every strategy's effective policy. The values come
  from the same `TRADE_RISK_PROFILES` registry and are checked by the same
  `size_reasons` helper the order gate calls, so the page cannot show a limit the gate
  would not apply. `GET /api/v1/trade-policy/preview` returns the server's sizing verdict
  for one proposed buy, including the lower of the absolute and percent-of-equity caps
  (`effective_max_usd`) and the stable codes `SIZE_BELOW_MIN`, `SIZE_ABOVE_MAX` and
  `EQUITY_UNKNOWN`. `PUT /api/v1/trade-policy/strategies/{id}` sets or clears a
  per-strategy profile; null inherits the deployment default, the engine passes the
  strategy's profile into the same `OrderRequest` the order gate resolves, and a change
  that loosens a fund-affecting limit is refused as `RISK_ACK_REQUIRED` unless the caller
  acknowledges it. Every write appends a `TRADE_POLICY_UPDATED` entry to the strategy's
  run log. The new `/settings/trade-policy` page renders the profiles and fields with
  their range and description, shows each strategy's effective profile and any sizing the
  profile would refuse, discloses a loosening inline, and shows the server's verdict
  (never a browser-calculated one). Migration `0023_strategy_risk_profile` adds one
  nullable `strategies.risk_profile` column and is additive.
- **Ledger hardening: exact minor-unit money, zero drift, and a tamper-evident tape
  (PAPER).** The ledger now has one documented money policy: USD is written as an
  integer number of 1e-8 minor units, computed values (derived notionals, bps fees, cost
  bases, partial-sell proceeds, the cost preview's worst case) are rounded half-even once
  at the ledger boundary, and explicit sub-minor values or floats fail closed with
  `SubQuantumError` / `FloatMoneyError` instead of being silently adjusted. Every
  conversion, product and sum derives its decimal precision from its operands
  (`exact_product` for a multiplication, `exact_sum` for a sum); the ambient 28-digit
  default is never trusted, so a notional with more digits than that converts exactly
  instead of rounding a minor unit away — a bare `quantity * price` rounds *before* the
  quantize and loses thirteen minor units at 22 integer digits. A venue receipt is the
  same: `journal_from_fill` quantizes its notional and fee once, so a partial fill whose
  notional is a repeating-decimal division result is journaled instead of crashing the
  pipeline after the money already moved. A journal group balances to exactly zero
  minor units with no tolerance, and the reconciliation read model compares money as
  exact integers — a persisted row off the quantum is reported, never compared at a
  coarser scale. The ordered tape gains a chained SHA-256 seal (`seal_entries` /
  `verify_seal`) over canonical JSON (`ezchain.ledger/v2`) that covers group, timestamp,
  wallet, agent bucket and actor, so no two distinct entries can serialize alike, plus a
  canonical `replay_balances`; changing any entry changes the head and the same tape
  always replays the same ledger. **The bps fee quantum moves from 1e-6 to 1e-8, so some
  fees previously journaled under the old rule change — and not only dust:** any fee
  whose exact `notional * fee_bps / 10_000` carries more than six decimals moves (30 bps of
  `0.000412` was `0.000001` and is now `0.00000124`; 30 bps of `0.0000033333` was
  `0.000000` and is now `0.00000001`). A fee already on a whole micro-dollar is unchanged
  (`0.30` stays `0.30`), and existing rows are not rewritten (the ledger is append-only).
  The worked table is in `docs/LEDGER.md` §2.1. The
  policy and the bank-style control matrix (double-entry completeness, reconciliation,
  immutability evidence and replay determinism implemented; period locks, on-chain
  verification with quarantine, the forensic audit command and — because no such path
  exists yet — a withdrawal/transfer journal, still open under GH-155) live in
  `docs/LEDGER.md` (ADR-0046).
- **Agent orders wait for a person, per user (PAPER).** Automation is now a per-user
  setting: `manual` (the default) holds every agent order in a durable approval queue,
  and `full_auto` executes it with no approval. `full_auto` requires acknowledging a
  plain-language risk disclosure, persisted with the user, the disclosure version and the
  timestamp. An approval has one lifecycle — `pending`, `approved`, `rejected`,
  `expired`, `superseded`, `executed`, `failed` — and every state names its next action.
  Only a person may approve or reject (an agent key is refused with
  `APPROVAL_REQUIRES_PERSON`), and the approver reads the server's own risk evidence, not
  agent text. Approving re-enters the one execution path with the stored intent, so the
  order keeps its original id and idempotency key and runs
  `Intent → RiskDecision → Quote → Order → Fill → Journal` like any other; a rejection
  refuses it with the person's reason. A pending approval expires after five minutes
  (`EZCHAIN_APPROVAL_TTL_SECONDS`), the queue survives a restart, and an unreadable queue
  or unreadable intent refuses closed with a reason code. New routes:
  `GET /api/v1/paper/approvals` and its approve/reject POSTs, `GET/PUT /api/v1/paper/automation`; the MCP order
  tool returns `approval_id` and `approval_status`, and `ezchain_list_approvals` /
  `ezchain_get_automation` expose the queue read-only. Migration `0021_approvals` is additive, sitting on `0020_market_events`.
- **The ops alerts read real state (GH-27).** `/metrics` now emits the four families
  `infra/prometheus/alerts.yml` alerts on, each sampled from live state on the scrape:
  `ezchain_source_freshness_seconds` (newest persisted observation per `source` and
  `source_class`, classified by the same `FreshnessPolicy` the order gate applies, with the
  matching `ezchain_source_freshness_budget_seconds`), `ezchain_breaker_state` per
  `provider` (0 closed, 1 half-open, 2 open, read from the live `CircuitBreaker`),
  `ezchain_queue_depth` (waiting callers on the bounded provider budgets and unpublished
  `market_outbox` rows) with the queue's own configured bound beside it as
  `ezchain_queue_capacity`, and `ezchain_ledger_drift_entries` (failing reconciliation
  checks). The alerts file names exactly these metrics and labels, and gains `SourceStale`
  (age past the class's own budget), `SourceFreshnessUnknown` (the `NaN` age of a
  clock-faulted source, which `>` and `absent()` both miss), `CircuitBreakerOpen`,
  `CircuitBreakerHalfOpenTooLong`, `ProviderQueueSaturated` (depth at the queue's emitted
  bound — a provider budget refuses a caller at `max_queue`, so the old `> 100` could never
  fire), `QueueDepthHigh`, `LedgerDrift`, plus an `absent(...)` guard per family so a metric
  that stops being emitted raises a warning instead of leaving a green board; each guard
  is conjoined with the signal that owns the outage (`up == 1` for the DB-independent HTTP
  counter, `ezchain_up == 1` for the four ops families, which are omitted exactly when the
  database is unreadable) so one outage pages once instead of every family.
  Because the four families read the database, an unreachable database omits them and
  reports `ezchain_up 0` rather than a confident zero. `/metrics` is served to a loopback
  client or to a request carrying the deployment token; it is never published by the edge.

- **One stable reason-code taxonomy, and a guard that keeps it honest (GH-28).**
  `ezchain_core.reason_codes` now holds one `ReasonSpec` per failure that can block or move
  money: 254 codes across 17 categories, each with its category, emitting layer, source
  module, operator summary, display message, HTTP status, retryable flag and severity. The
  older registries are derived from it rather than copied — `RISK_REASON_MESSAGES`,
  `VAULT_REASONS` and `BINDING_REASONS` are built from the single registry — so a message
  or a membership test has exactly one home, and the API imports the codes from there
  instead of re-typing ~20 literals. `docs/REASON-CODES.md` enumerates every code with its
  meaning and maps each to the `Intent`/`Order` state it explains and to the surface the web
  renders. The stability promise is explicit: a code's string and meaning are permanent, its
  message is not, and callers compare on `code`. `tests/test_reason_codes.py` is the drift
  guard: it scans the AST for every shape this project emits a code through and fails when a
  code is emitted without a row, when a row names a code nothing emits, when a row is
  missing from the document, or when the document lists a code the registry does not
  define. The audit it enabled found and fixed a missing message (`MISSING_RISK_INPUTS`),
  removed three homes for one spelling (`SNAPSHOT_STALE`) and kept the codes that only look
  duplicated (`KILL_SWITCH_ARMED` vs `KILL_SWITCH_ACTIVE`, `NO_ROUTE` vs `NO_SELL_ROUTE` vs
  `EXIT_NO_ROUTE`) with the difference documented. Refusals themselves are unchanged; only
  the vocabulary moved. Recorded as ADR-0043.
  the vocabulary moved. Recorded as ADR-0042.
- **Custody is the user's, and the vault keeps keys from everyone else (PAPER).** Keys are
  added at runtime by the person who owns them, encrypted at rest under that user's
  credential, and used only for that user's orders. `ezchain_core.vault` owns the contract:
  a `KeyHandle` (opaque id, owner, custody tier, chain, public address, public-key SHA-256
  fingerprint) and a `Vault.sign(prepared, handle, owner=...)` that returns a
  `VaultSignature` and has no operation — and no field — a key, seed or passphrase could
  travel in. Custody tiers are `local_vault`, `hardware` (a device, one option among
  several, not the only path) and `watch` (never signs). `services/executor` implements a
  vendor-agnostic `HardwareSignerVault` over a `SignerTransport`, so a USB device, a local
  daemon or a remote signer is interchangeable and no vendor SDK is a dependency. Fail
  closed: an unreachable signer, a declined approval, an unknown handle, another user's
  handle, a locked signer, a watch-only handle, a device answering for another key or a
  broken transport each produce no signature and one typed `VAULT_*` code; another user's
  handle refuses exactly like an unknown one. Automation is a per-user `AutomationSetting`:
  `manual` is the approval queue and `full_auto` cannot be constructed without the opt-in
  risk disclosure for that same user, with the key-add disclosure recorded beside it. An
  intent that names an automation runs unattended, so the pipeline resolves that user's
  setting and refuses with `AUTOMATION_CONSENT_REQUIRED` when there is none, when it belongs
  to another user or when the tier still waits for per-order approval — before any order
  exists. Live also requires custody, not just a signing boundary: a pipeline whose mode is
  not paper refuses a signer with no signable per-user handle (`VAULT_BOUNDARY_REQUIRED`)
  before the venue, and a `handle` that is present but not a `KeyHandle` is refused on the
  same code instead of raising. The order's principal travels on the intent and into the
  approval, so the pipeline compares it with the handle's owner before any signer runs: one
  executor instance cannot sign user B's order with user A's key (`CUSTODY_OWNER_MISMATCH`),
  and `VaultTransactionSigner` no longer takes a caller-asserted `owner`. Paper runs
  unchanged with the fake venue. Recorded as ADR-0040.
- **Market observations persist to Postgres, with retention and an outbox (GH-26).**
  Revision `0020_market_events` adds `market_events` and `market_outbox`, forward-only and
  additive. `market_events` is one row per `ezchain_core.MarketEvent` keyed by the contract's
  own idempotency identity `(source_name, pair, source_observed_at)` — the triple replay
  dedupes on — so a retried write cannot duplicate an observation, and
  `ezchain_ingest.store` returns the same `MarketEvent` values a read model already knows
  how to age. One `market_outbox` row is appended in the same transaction, so a consumer is
  never told about a write that rolled back; an unpublished row is never pruned.
  `ezchain_ingest.retention` holds the two windows in one validated place (raw 7–30 days,
  1-minute candles a year) and prunes them through `python -m ezchain_ingest`, a dry run by
  default. The JSONL recording stays the replay input. When the server has `timescaledb`
  installed (or this role may install it), `market_events` is additionally converted to a
  hypertable partitioned on its source timestamp; an installable-but-forbidden extension is
  a clean fall-back, and on the shipped plain `postgres:16-alpine` the tables are ordinary
  and every guarantee above still holds. Retention, backup and restore are documented in
  `docs/RUNBOOK.md` §12.8.
- **The executor signs only the intent that was approved (PAPER).** A venue adapter now
  receives an immutable `ApprovedIntent` (chain, cluster, mint, side, amount, minimum
  output, recipient, program id, idempotency key) and returns bytes; it is not handed the
  order or the quote. The execution pipeline itself decodes those bytes and compares every
  field with the approval before any signer runs, so a substitute or buggy signer cannot
  disarm the binding — a swapped mint, inflated amount, changed recipient, foreign
  program, unexpected cluster, wrong side, lowered output floor or replaced idempotency
  key refuses with its own `TRANSACTION_*` code and no signature. The signature that comes
  back must carry that same approval and cover the bytes that were bound
  (`TRANSACTION_APPROVAL_MISMATCH`, `TRANSACTION_DIGEST_MISMATCH`), and the signed
  payload's SHA-256 digest travels through submission so a receipt that does not echo it is
  refused and never journaled. A pipeline in a mode other than paper refuses an unsigned
  submit (`SIGNING_BOUNDARY_REQUIRED`) before the venue is called. The signer and key ring
  stay in `services/executor`; the fake venue is the signing boundary for tests.
- **A self-hosting guide for a server the operator owns.** `docs/SELF-HOSTING.md` covers
  server sizing, the one-command installer and its verify-before-extract flow, firewall
  and TLS placement, the PAPER-only stack, where the generated secrets live, rerun and
  upgrade behaviour, backup and restore, and the shared-origin trust model. It states
  plainly that the project never holds, sees or signs with trading keys, that nobody
  should give anyone access to a server holding keys, and that live mode and its key path
  are not available yet.

- **One execution pipeline from intent to journal (PAPER).** `services/executor` now owns
  the single `Intent → RiskDecision → Quote → Order → Fill → Journal` path against a venue
  behind the core `Venue` protocol, with the typed stages in `ezchain_core.execution`.
  Paper, testnet and live run the same runner; only the venue adapter and the custodian
  differ. It fails closed on money (no snapshot → no buy, no quote → no order, stale quote
  → denied, unreadable kill switch → refused), dedupes duplicate intents to one order and
  one fill, sizes a reduce-only exit to the held position, and books the journal from the
  venue receipt through an append-only sink. A fake venue covers a fill, a rejection, a
  timeout, a partial fill and a no-quote in the acceptance suite.

### Changed

- **The review a pull request needs is now decided from its diff, not from a label
  (ops).** `tools/check_reviews.py` classifies a diff as money-path when it touches
  `packages/core-py/`, `apps/api/alembic/`, `services/engine/`, `services/executor/` or
  `packages/adapters/`, and requires two distinct approvers there and one everywhere else.
  An approver is one GitHub login — an `APPROVED` review or a maintainer comment whose line
  begins with `approved` — so a reviewer who comments twice is still one reviewer, and a
  bot login is none. An unreadable thread exits `1`; the merge gate refuses on any
  non-zero exit, so an outage cannot read as consent. The gate itself now calls the
  checker for every PR instead of grepping comment bodies for "approved". `0xJ1nn` is
  currently the only reviewing account, so a money-path PR is refused until a second
  account with write access approves it; [WORKFLOW §3](docs/WORKFLOW.md) says exactly that.

- **The dependency set is pinned, hashed, audited and reproducible (security).** The three
  Python range files now have generated locks — `requirements.lock` (43 pins),
  `requirements-runtime.lock` (31) and `requirements-keyring.lock` (9) — each entry an exact
  version with `sha256` for every artifact; the dev setup and the API image install them
  with `--require-hashes`, and `pnpm-lock.yaml` carries an `integrity` hash per resolution.
  `tools/check_pins.py --fast` is step 6 of `tools/ci.sh` and fails on a range its pin no
  longer satisfies, an unhashed pin, or a Node resolution without integrity; its `--full`
  path installs a lock into a fresh venv and fails on any drift, and `--full --offline`
  does the same from a wheelhouse with no network. `tools/audit_pins.py` exits 1 on an
  advisory that is not allowlisted with a written reason and exits 2 (never 0) when the
  audit could not run. Both Dockerfiles now name their base image by manifest-list digest
  with the tag it came from in a comment, and the gate refuses a bare `FROM` or a digest
  with no tag. Every dependency a package pyproject declares must be pinned; the one that
  cannot be — the firehose extra's git tag — is named with its reason in
  `tools/pins/unpinned.txt` instead of being given an invented version.
  `pnpm.overrides` moves the transitive `postcss` inside `next` from
  8.4.31 to 8.5.33, which clears two high and two moderate advisories. The update ritual,
  the re-audit cadence and the honest list of what was not verified are in
  [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md) and
  [docs/evidence/dependencies/pins.md](docs/evidence/dependencies/pins.md).

- **The live pump.fun source is an opt-in extra, so the API image stays free of it
  (ingest).** `pumpfun-firehose` was pinned in `requirements.txt` and
  `requirements-runtime.txt`, which made every consumer — the API image included — install a
  package it never imports. The adapter duck-types its events, so the dependency now lives
  only in `ezchain-ingest`'s `firehose` extra (`pip install "ezchain-ingest[firehose]"`),
  pinned to the firehose repository's `v0.5.0` release tag rather than a pull-request head.
  `ezchain_ingest` imports and the retention CLI runs with no `pumpfire` installed; the
  live `FirehoseSource` imports it on first construction and names the extra when it is
  missing. The firehose fixture tests skip when the extra is absent.

- **Owner history pages past the newest slice (api).** `GET /paper/orders`,
  `GET /paper/fills` and `GET /paper/ledger` accepted only `limit`, so the newest 200/500
  rows were all an owner could ever read back. All three now accept `offset` alongside
  `limit`; orders and fills break a `created_at` tie by id so pages neither overlap nor
  skip a row, and the ledger pages on its unique `seq`.

- **An order may name any defined risk profile (api).** `POST /paper/orders` used to
  accept a profile only when it equalled the deployment's, so the client offered a choice
  the server refused. Any of `low`/`mid`/`high` now shapes that order's gates; the
  deployment profile is only the default when the request names none, and an unknown
  name is still `PROFILE_UNKNOWN`. The profile the owner chose owns its own order bound —
  `high` refuses an order above $250 — and its percent-of-equity, per-token and slippage
  policy applies with it. The deployment's `max_order_usd` is the ceiling only for an
  order no profile shapes; it is never `min()`-combined with a profile, so choosing a
  profile neither silently widens the preset nor shrinks a ceiling above it.
  `GET /paper/limits` resolves `max_order_usd` through the same function, so the number
  the UI shows is the one the order gate enforces.

- **A strategy's profile is enforced, not advisory (api).** The order gate read the
  requested profile from the payload only, so an order naming a strategy pinned to `low`
  could still be placed at `high` through the route or a direct `paper.place_order` call.
  Precedence is now decided once, in `paper.resolve_order_profile`: the strategy row's
  profile is the bound, a per-order `risk_profile` may only tighten it, and
  `settings.trade_risk_profile` is the default when neither names one. A looser per-order
  profile on an entry is refused as `PROFILE_LOOSENS_STRATEGY` (a stored rejection on the
  order row, not an HTTP error); a sell is exempt because the bound is an entry gate. A
  strategy with no profile still follows the deployment settings.

- **Opting into a profile is measured against the deployment ceiling (api).** A change was
  compared to "no profile", which read as the fewest gates, so a strategy with no profile
  could take `high` ($250) over a deployment capped at $100 per order with no
  acknowledgement. The loosening test now compares the enforced per-order ceiling, with
  `None` meaning the deployment's own `max_order_usd`, so a profile that admits a larger
  order needs `RISK_ACK_REQUIRED`. Every other profile field only adds a gate (a minimum, a
  percent-of-equity ceiling, a per-token cap), and `max_position_usd` /
  `max_price_impact_pct` keep applying beside a profile, so neither is widened by one. The
  `TRADE_POLICY_UPDATED` audit entry now records the stored override before and after, both
  effective profiles, the principal and whether the loosening was acknowledged. The
  settings page mirrors the same rule and asks for the acknowledgement when it cannot prove
  a change only tightens.

- **A page offset is bounded (api).** `GET /paper/orders`, `GET /paper/fills` and
  `GET /paper/ledger` accept `offset` up to 10000; a larger value is `422`. The newest
  page and every page the UI offers stay reachable, and a paging client cannot ask the
  database to skip an unbounded row count.

- **The kill-switch GUI releases in one step (web).** The Risk card and the top-bar
  control required a non-blank reason before either direction, so releasing — the
  direction that gives the owner their trading back — was gated in the browser while the
  API accepts an optional reason in both. Arming still asks for a note as bookkeeping;
  releasing sends at once, and a blank reason is sent as `null`.

- **Run-now works on a paused strategy (web).** The list disabled the control whenever a
  strategy was paused, hiding the API's manual cycle. It stays enabled and is labelled
  "Run one cycle (manual)": one cycle runs, the strategy stays paused, and resuming is
  still what re-enables the scheduler.

- **Activity pages its orders and fills (web).** The ledger view read the newest 100 rows
  with no way forward and now carries Prev/Next for its orders and fills tables, using
  independent offsets that the API bounds. `getPaperOrders`/`getPaperFills` send `offset`
  in live mode and slice the same way in fixture mode.

- **A manual run is not a scheduled tick (api).** "Run now" on a paused strategy used to
  refuse with `409 STRATEGY_PAUSED`. The pause now gates automatic scheduling only: an
  explicit owner request runs exactly one cycle (even inside its interval), and the
  strategy stays paused, so the scheduler keeps skipping it. Resuming separately is still
  what re-enables scheduling.

- **The kill switch arms and releases symmetrically (api).** Releasing required a reason
  while arming did not, gating the direction that gives the owner their trading back. Both
  directions are now one step with an optional reason, which is stored when supplied; the
  admin scope and bearer requirement are unchanged.

- **The topbar kill switch follows the persisted state instead of the page snapshot
  (web).** The shell read the flag once when a page rendered, so arming or releasing
  from the Risk page left the topbar showing the state it had loaded with, and the two
  controls could disagree. Both controls now read the same client store: the mutation
  response from either one updates the other in place, with no reload. The confirmation
  names the scopes the API returned after the change, and when the API returns no state
  the launcher says UNKNOWN, names no scope and promises no action it cannot know.

- **Market read models carry the contract's source and read-time age.** `GET
  /api/v1/markets` rows, `GET /api/v1/markets/stored` rows and the token header now report
  provenance from the one `MarketEvent` contract instead of whatever the adapter answered.
  Rows gain `age_seconds` and `stale`, both derived from the row's `SourceRef` against the
  read clock, plus a structured `source` object on the stored list (it was a bare name).
  The token header keeps its boolean `stale` but no longer trusts the adapter's flag, and
  gains `age_seconds`. A row whose persisted source is blank or `unknown` has no
  observation behind it, so `source`, `age_seconds` and `stale` are all `null` on the
  stored list rather than a fresh-looking zero; a token with no source reports `stale:
  false` and a null age. PnL is still replayed from the ledger, never recomputed here.

- **There is one market event, and the pump.fun firehose adapter emits it.** The adapter
  no longer defines its own `MarketEvent`; `normalize_firehose_event` returns a
  `FirehoseEvent` carrying the core `ezchain_core.MarketEvent` observation plus this
  wire's pump.fun extras (kind, signature, slot, token metadata), and its `to_dict`
  composes the contract's serialiser. Contract fields are read under the contract's names
  (`pair`, `price`, `size`, `direction`, `units`, `source`, `observed_at`) rather than
  `mint`/`price_sol`/`size_sol`/`side`; `mint` remains in the serialised body as the wire
  alias. **The serialised snapshot body no longer contains `side`, `price_sol` or
  `size_sol`; consumers must read `direction`, `price` and `size` instead.** A firehose
  observation now records and replays through `services/ingest` unchanged, with age derived
  from the reader's clock.
- **A stale source fails closed and raises one alert per transition (DATA).** Freshness is
  now a per-source-class policy (`ezchain_core.freshness`: `mark`, `quote`, `candle`,
  `feed`) that classifies a read as `fresh`, `stale`, `unknown` or `future`, and only a
  fresh read prices. The mark path had a hole: `TokenSnapshot.stale` was hardcoded `False`
  in every production constructor, so `SNAPSHOT_STALE` could only fire from a test. The
  budgets from `EZCHAIN_MAX_*_AGE_SECONDS` now reach every snapshot producer (order path,
  markets, graph, candles, market monitor) and the order gate, so `stale` is a real age.
  The vocabulary is one per `MarkStatus`: an old observation is `stale` with
  `SNAPSHOT_STALE`; a timestamp beyond the tolerated skew is the contract's `unknown` clock
  fault with `MARK_TIMESTAMP_FUTURE`, never mapped onto a stale reason. An entry from a
  stale mark is a persisted `SNAPSHOT_STALE` rejection row; a transient route failure still
  raises without claiming the idempotency key. A reduce-only exit is never trapped: it sizes
  from the held quantity, prices from a fresh venue quote on a venue chain, and settles at
  the last observed mark on a `paper_mark`-only chain — while every stale mark stays out of
  equity, market value and unrealized PnL (the account is incomplete, never stale-priced).
  Adapter caches no longer re-stamp a cached payload with the read time, so a
  15-second-old answer keeps its real age. A source that goes stale raises `provider_stale`
  once per transition, keyed `(chain, token, kind)` and drained into `GET /paper/alerts`
  through a bounded queue; the transition state is in memory, so a restart re-raises once
  per process. Tests cover the exact budget edge, stale-on-read, stale-mark exits on both
  chain kinds, a persisted entry refusal, and recovery.

- **Paper wallet balances are exact and `GET /paper/wallets` only reads (PAPER).** Wallet
  cash now comes from the same Python `Decimal` replay that computes the account's cash,
  bucketed per wallet in one pass, instead of a SQL `SUM` over the cash account. SQLite
  stores money as TEXT, so that sum coerced every row to REAL and the wallets drifted from
  the account around the 13th decimal place; a seeded random-walk property test now proves
  `sum(wallet.balance_usd) == replay.cash` after every step. The wallet route no longer
  declares wallets — they are seeded at boot and on order placement — so a GET cannot
  write. Cash or positions whose wallet row is missing stay visible in `by_wallet`'s
  `unallocated` row instead of disappearing from the total. A buy through
  `POST /paper/orders` on a chain without a wallet is refused with the new
  `WALLET_UNKNOWN` reason code before a quote (a wallet that does not settle in the
  ledger asset is refused with `SETTLEMENT_ASSET_UNKNOWN`); a sell is never refused for
  it and settles to the settlement wallet. Decision recorded in ADR-0032.

### Fixed

- **The executor honours the kill switch at sign time, before the key is used (PAPER/GH-227).**
  The pipeline read the switch before the venue, after the quote and once after the
  signature, so an arm that landed while the unsigned transaction was being built was
  obeyed only by discarding a signature the signer had already produced. The read now also
  runs after the decoded transaction is verified and immediately before the signer is
  called, so an intent already in the pipeline at the moment of arming is refused with
  `KILL_SWITCH_ACTIVE` (or `KILL_SWITCH_UNAVAILABLE` when the state cannot be read) without
  a signature existing at all. The gate sits in the pipeline rather than in the signer, so a
  signer that consults nothing cannot disarm it, and it stays entry-only: a reduce-only exit
  re-bound against the held position still signs and settles. The paper order service is
  unchanged, so manual paper orders behave exactly as before. Decision recorded in ADR-0045.
- **An agent key reads its own bucket and no other.** `GET /api/v1/agent/buckets` and the
  per-agent route answered every capped agent to any caller holding a `read` scope, so one
  compromised key could map the whole desk's cap and deployment before sizing a trade. The
  read is now scoped to the authenticated principal: a person still sees every capped agent,
  and an agent key sees one row — its own. Another agent's id answers `404
  AGENT_BUCKET_UNKNOWN`, deliberately the same answer as an id that was never minted, so the
  refusal does not disclose that the neighbour exists. The route is also read as the caller
  rather than anonymously when a credential is supplied; an absent one still reads the
  workspace view, so a self-hosted deployment without a token is unchanged. Found by the
  prompt-injection suite, which now fails if the scope is removed (`docs/PROMPT-INJECTION.md`).

- **One decision window yields one strategy run and one execution (GH-129).** A strategy's
  interval gate is read from `strategies.last_run_at` before anything is committed, so two
  evaluators of one strategy — two scheduler ticks, an inline tick and a manual run, or a
  process restarted while still holding a row that believes `last_run_at` is null — could
  both clear it and both decide. The order idempotency key collapsed the fill, so no extra
  money moved, but `strategy_runs` reported two decisions for one order and a second
  evaluation's reasoning was logged as though it had run. The run insert is now the claim:
  `strategy_runs.decision_window` names the interval bucket and the unique
  `(strategy_id, decision_window)` pair lets exactly one evaluator commit it, before the
  order is placed. The evaluator that loses the claim never reaches the executor and
  reports the winner's record instead of its own; `last_run_at` moves in the claim's
  transaction, so a claim that did not commit consumed nothing. A row that consumes no
  interval — `INTERVAL_NOT_ELAPSED`, `MARKET_DATA_UNAVAILABLE` — leaves the window null and
  stays retryable inside it, a fail-closed refusal (`KILL_SWITCH_ACTIVE`,
  `ORDER_REJECTED`, `EXECUTION_FAILED`, `ORDER_EXECUTOR_UNAVAILABLE`) releases the claim it
  took and restores `last_run_at`, and a wait keeps it because the wait is the window's
  decision. `RunResult.amount_usd` is `None` for a caller that lost the window: the log
  holds no resolved size for it, and an unknown amount is not zero. Reconciliation gains
  `strategy.runs_linked`, which reports a live strategy's order that no run row names (a
  crash between the order and the log) and treats several rows for one released window as
  the log working. Migration `0025_strategy_run_window` is additive on
  `0024_agent_buckets`, backfills nothing, and preserves the table's indexes through the
  SQLite batch rebuild. Decision recorded in ADR-0046.

- **Discovery claims only the coverage it has, and labels trending by its true scope
  (GH-119).** Solana had no entry in `SEED_TOKENS`, so its discovery depended on the
  promotional profile/boost/takeover feeds while `chain=all` looked like a seeded scan of
  every advertised chain. Solana now carries the canonical wrapped-SOL, USDC, BONK and JUP
  SPL mints, so an empty hint feed still returns Solana rows and every registered chain is
  asked about directly. Coverage gained `no_seeds` for a chain with no seed or hint address:
  nothing was requested, so its population is unknown, not `no_pairs` (which keeps its true
  meaning, the provider was asked and returned nothing). The market monitor no longer
  records such a chain as an empty observation or a successful scan, and `chains_scanned`
  does not count it. Ordering is named per its real scope: an all-chain browse is
  `per_chain_round_robin`, a feed-wide search is `global_sort`, and a single-chain read is
  `per_chain_sort`; the markets page badge/footer and the dashboard trending strip say so
  instead of calling a within-chain ranking global. Coverage also exposes `timeout` in the
  API shape instead of failing serialization. The markets empty state branches on that
  coverage: a chain with no discovery seeds is labelled NO_SEEDS and says nothing was
  requested (unknown, not an empty result), while a genuine empty feed keeps EMPTY_RESULT.
  A `timeout` coverage entry is recorded as a failed attempt with its own "all-chain probe
  timed out" reason, so the overview reports error instead of a successful empty `no_data`.
- **The risk dashboard reads a mark's freshness from the server, not from a 60s rule
  (GH-115).** `/risk` re-derived stale/fresh from `source.observed_at` with a client-side
  `STALE_AFTER_SECONDS = 60`, while the API ages the mark with the configured
  `max_mark_age_seconds`; a mark fresh to the page could be stale to the order gate, and a
  stale mark (whose price the server withholds) was counted as an unknown mark. The page now
  labels and counts every row from `PositionView.mark_status` and `mark_age_seconds`, and
  the unknown count uses `mark_status == "unknown"` so a withheld stale price is not
  reported as a missing one. The web fixtures carry the server's verdict explicitly instead
  of computing it from an age, and one row demonstrates the divergence (45s old, server
  says stale). ARCHITECTURE §4.5 now names `mark_age_seconds` as the field on the wire.
- **The approval gate follows the automation, not the actor label (PAPER).** An agent key
  with `trade` could create a strategy and run it with no approval, because every
  `strategy_id` resolved to `ACTOR_STRATEGY` and the gate only looked at `ACTOR_AGENT`.
  Strategies now record their creating principal (`0022_strategy_creator`); an
  agent-created strategy is gated like a direct agent order, a person-created one is not,
  and unknown provenance fails closed. The tier and its consent are bound to the ordering
  principal's owner instead of `workspace_id`, so user A's `full_auto` does not authorize
  user B's agent orders; a stored `full_auto` without its consent record refuses
  `AUTOMATION_CONSENT_REQUIRED` rather than being read as `manual`; and the queue-health
  probe rolls back a failed read so the typed `APPROVAL_STATE_UNREADABLE` refusal can
  still commit.
- **The dashboard risk card names the operator switch instead of calling it all risk
  (GH-132).** The card reported "Risk OPEN/HALTED · kill switch · strategy scope", while
  the risk page says an armed switch still lets exits settle and never touches a position.
  A reader could take "Risk OPEN" as "all new risk is allowed". The KPI is now "Kill
  switch" with the persisted state and the levels the API reports, and the risk panel
  shows one row per reported level (`global`, `strategy`) with what that level gates: the
  global level refuses every new entry before a quote, the strategy level holds
  strategy-attributed intents with `KILL_SWITCH_ACTIVE`. The card states that manual paper
  orders are entries, so a manual paper buy is refused while armed, and links to the risk
  page's "what it does not do" explanation. Both surfaces read the level names and the
  gating sentences from one module (`lib/kill-switch-copy.ts`), and the card follows the
  shell's kill-switch store, so arming from the topbar moves it without a reload. A read
  that returns no state renders UNKNOWN, and a read without `scopes` names no level
  instead of guessing both.

- **A quote timeout counts against the provider's breaker, and a cancelled market scan
  releases its half-open permit.** The router's attempt deadline cancels the inner quote,
  which reaches the provider as cancellation; the breaker recorded no failure for it, so a
  provider that hangs instead of erroring never opened and every order paid the full wait
  again. The router now tells the provider when a deadline it owns elapses
  (`record_timeout`), for both the entry quote and the mandatory full-size exit, and the
  circuit opens as it does for a 5xx. The market monitor's scans take the one half-open
  permit before probing and record an outcome only when the probe completes, so a scan
  cancelled mid-probe (shutdown, a superseded manual scan) held the permit for ever and
  every later scan was rejected as `circuit open`; `run_once` now releases the permit on
  cancellation. A cancelled call is still not a failure, and a `no route` answer still does
  not count.

- **The dashboard states the launches window and the execution mode it actually has
  (web).** The launches panel called its column "Age" next to the trending table's age and
  listed whatever the feed returned, including pairs born days earlier, so the "newest
  births" heading promised a window the query never applied. It now asks the API for pairs
  born inside a 24-hour window (`max_pair_age_minutes`, exposed as `LAUNCH_WINDOW_MINUTES`),
  labels the column "Pair age", and uses the pair-age formatter that marks a newborn pair.
  Pairs whose birth the source omits are excluded, not assumed newborn, and an empty window
  says so instead of being backfilled. The topbar mode chip was literal `PAPER`; it now
  reads the server-derived mode from `/healthz` (`mode.execution`) the way the kill-switch
  store reads its state, and renders UNKNOWN with the reason when the read fails or the
  payload carries no recognised mode — it never falls back to a guess. The equity KPI
  value no longer breaks mid-number at desktop widths (it takes a wider share of the band
  and does not wrap; the wrap breakpoints still apply).

- **Every mode claim on the dashboard comes from the one server read, and the equity tile
  is proven contained at phone widths (web).** The previous pass left the page deriving
  its mode badges from the data-source mode rather than the execution mode, so the equity
  composition could badge PAPER while the topbar chip said UNKNOWN, and its action row
  still rendered a literal PAPER beside FIXTURE. The page now reads the execution mode
  once, through the same `getExecutionMode` the chip uses, and every badge, the Mode KPI,
  the strip footer and the prose that names a mode render that value: UNKNOWN with the
  reason when the read fails or the API reports no mode, and no hardcoded mode display
  string anywhere on the page. The equity value is now proven contained rather than
  assumed: at 1440, 1200, 390 and 360 the glyph rect must sit inside its tile and the tile
  inside the band, measured on rects — the old `scrollWidth == clientWidth` check could
  never fail on a `nowrap` node. That proof found a real clip: the hero's unbounded
  `min-width: max-content` pushed its tile past the band, whose `overflow: hidden` cut the
  digits off, so the minimum is now capped at the band's width and the phone breakpoint
  scales the hero type to fit. The `UNKNOWN` chip case is gated on `EZCHAIN_WEB_MODE=live`
  like its sibling, so a fixture run no longer fails on an assertion it cannot satisfy.

- **The MCP server refuses writes without a verified scoped credential, from every entry
  point.** The deployment token is an admin principal, so an agent must never run with it.
  The startup probe already refused it, but the guard lived in the CLI: a server
  constructed directly, in a test or another host, could still open the write surface.
  `McpServer` now derives `allow_writes` from a credential it verified itself, so a
  directly-constructed server without one is read-only, and a named write tool returns
  `WRITES_DISABLED` with the operator instructions instead of running. The probe also
  classifies on the typed error code now: only `403 FORBIDDEN` means "authenticated,
  lacks admin", so a bare `403` — the web proxy's `CROSS_ORIGIN_DENIED`, a WAF rule — no
  longer opens writes for the deployment token.

- **An armed kill switch refuses every new entry, whoever places it (PAPER).** The order
  service checked the switch only for strategy-attributed orders, so a buy placed through
  the REST route, the MCP server or the web ticket — none of which carries a strategy —
  still filled while the switch was armed. The order service now refuses any buy with the
  new `KILL_SWITCH_ARMED` reason before the market read and the quote, and persists no fill
  or journal entry. Sells are never refused: an exit can only reduce risk, so reduce-only
  sells and stops still settle instead of trapping funds. The strategy level remains the
  narrower second layer in the engine (`KILL_SWITCH_ACTIVE`), and the switch endpoint now
  reports both levels (`scope: "global"`, `scopes: ["global", "strategy"]`). Decision
  recorded in ADR-0034.

- **The topbar describes only the market search it performs.** The field is
  labelled for a market symbol, name or address and submits to the Markets
  screener; a wallet, strategy or position term is treated as a market query
  and returns the screener's own empty result instead of implying a global
  search. The display-only "All chains" chip is removed from the topbar and the
  More page; the More page now explains that chain scope belongs to Markets,
  Launches and Positions, and links to Markets.

- **A market search no longer claims it scanned every chain.** The Markets
  footer describes a symbol, name or address query as a feed-wide market query,
  which is what the feed returns; the API reports no per-chain coverage for a
  search.

- **Protection writes are compare-and-swap and the event sequence is
  serialized (PAPER).** Engine ticks and API edits increment the arm version in
  the database and reload from the winner's row when they lose, so a concurrent
  edit is never overwritten and the version never moves backwards. Event
  appends take a per-arm lock before reading the next sequence, so two
  transactions cannot choose the same sequence and roll back one arm edit.
  Bulk `UPDATE`/`DELETE` against the audit table are refused at the ORM seam,
  and an update event records the rule it replaced.

### Added

- **Operator-supplied provider credentials (PAPER).** `POST /api/v1/provider-keys` stores a
  provider key through the configured secret store — an `age` recipient or the OS keyring, with
  no plaintext fallback — and returns only a fingerprint; no route returns the key and no log
  line or error body carries it. Rows are workspace-scoped, one key is active per provider, and
  revocation keeps the stored reference until the external delete succeeds, so a retry finishes
  the purge instead of orphaning a secret. The provider budget now bounds a caller's cumulative
  wait by the quote attempt's deadline, so an exhausted budget returns the typed 429 rather than
  a route failure. Decision recorded in ADR-0033.

- **Paper wallets and per-chain settlement (PAPER).** Every paper-capable chain now has
  a wallet besides the genesis settlement wallet. The cash legs of an order are
  attributed to the wallet for that chain, and a wallet's balance is the sum of those
  rows: the wallets add up to the paper cash account and no balance is stored twice.
  `GET /api/v1/paper/wallets` lists them, and `/paper/account` decomposes equity into
  each wallet's settled cash plus its chain's marked positions. An order on a chain with
  no wallet is refused with `WALLET_UNKNOWN`; a wallet that does not settle in the ledger
  asset is refused with `SETTLEMENT_ASSET_UNKNOWN`. Migration `0015_paper_wallets` adds
  the wallet table and the cash-leg attribution. Existing ledger rows are unchanged and
  count towards the genesis wallet.

- **Protection audit trail and optimistic versioning (PAPER).** Every arm
  transition appends an immutable event (rule snapshot, mark, observation time,
  action, resulting order id) in the same transaction as the state change it
  describes, and `GET /api/v1/protection/arms/{id}/events` returns the trail
  newest first. Arm rows carry a version; updates and cancels that supply an
  expected version return `409 PROTECTION_CONFLICT` when another writer won the
  race. Migration `0012_protection_events` adds the event table and the version
  column. Nothing in the order path changed.

- **Quick-trade bar on the token page (PAPER).** Presets of 50, 100 and 250 USD plus
  any custom notional the browser remembers, a buy/sell toggle, and one paper submit
  through the existing order endpoint. The landed pre-trade cost sheet renders before
  the click, and the bar repeats the quote's route, source and age. After a filled
  buy, an explicit `Protect this position` action opens a prefilled stop-loss form;
  arming is never automatic.

- **Position protection arms and profiles (PAPER, issue #103):** stop-loss,
  trailing stop (percent or price distance, above-water only) and take-profit
  (single or laddered) on any open position, each immediately editable and
  removable, plus manual and smart profiles (global, with per-chain override).
  Arms evaluate on engine ticks and close through the same paper order path as a
  manual sell, journaling like any other fill. Migration `0009_protection` adds the
  `protection_arms` / `protection_profiles` tables; the web adds arm controls on
  Positions and an armed list on Automations. No live execution is added.
- **Ledger monotonic sequence for deterministic replay (PAPER).** `ledger_entries`
  now carries a DB-assigned monotonic `seq` (identity/bigserial on Postgres, unique
  and indexed). All ledger reads and replays order by `seq` instead of
  `created_at, id`; `/paper/ledger` and the CSV export expose `seq`; newest-first
  listings use `seq DESC`. Migration `0010_ledger_sequence` backfills existing rows
  deterministically in `(created_at, id)` order. No PnL or cost-basis math changed.

- **Positions workspace display modes and per-position detail route (PAPER).** Positions
  now render the same server rows in List, Grid and Detailed modes selected via
  `?view=`, with chain filter and sort persisted in the URL. A new
  `/positions/[chain]/[token]` route shows full position facts, the landed candle chart
  with timeframe control and named reason states, recent fills, and read-only
  protection-arm status. A sell action is offered only when the token resolves in the
  registry; otherwise an explicit unavailable reason is shown. Candle blocks and the
  protection panel on the positions page survive the redesign.

- **Sourced fee table and structural edges in the README (PAPER).** A fee-math
  table quotes each terminal's own published per-trade platform fee with its
  source page and read date, contrasts it with a zero-platform-fee self-hosted
  PAPER workstation, and is followed by six evidence-anchored structural edges
  and an honest-limits section. No code changes.

- **Market-first discovery:** the dashboard leads with a paper-labelled launches and
  trending strip above equity, and a new Launches births feed lists recent tokens with
  age, curve progress and dev history and safety marked unknown until they have a
  source, with one-click Trade per row. In fixture mode every row is labelled FIXTURE
  sample data.

- **Strategy SDK v1 and supervised plugin runtime**: new Apache-2.0 `ezchain-sdk`
  (manifest, JSON-Schema subset, `PluginContext`, `Intent`, `Strategy`), discovery through
  `ezchain.strategies` entry points and `EZCHAIN_PLUGIN_PATHS`, and `kind=plugin`
  strategies. Plugin code runs in a separate worker with a scrubbed environment, memory/
  CPU limits, a per-callback timeout and crash quarantine. `ctx.state` is persisted with
  the run and replayed; one intent per tick; reserved execution fields fail closed.
  Migration `0007_strategy_plugins` adds the pinned `plugin_id`/`plugin_version`.
- **Plugin API and agent surface**: `GET /api/v1/plugins`, `POST /plugins/reload`,
  `POST /plugins/{id}/validate`, `GET /api/v1/agent/state`, `GET /api/v1/agent/capabilities`
  and `GET /api/v1/strategies/{id}/live-readiness`. New `apps/mcp` implements MCP
  2025-11-25 over stdio with read tools/resources and paper-only mutation tools; writes
  are hidden unless `EZCHAIN_MCP_ALLOW_WRITES=1` and still require the API bearer token.
- `docs/STRATEGY-SDK.md` is now the implemented contract; `docs/AGENT-INTEGRATION.md`
  documents Hermes/OpenClaw setup and `docs/RESEARCH-2026.md` records the competitor,
  trader and agent evidence behind ADR-0028.
- **First-class Hermes and OpenClaw packs**: `integrations/hermes` installs the
  `ezchain-trading` skill and prints the MCP registration; `integrations/openclaw` is an
  Agent Plugins 1.0.0 bundle that OpenClaw detects as `format: bundle` with skills and an
  MCP server. Verified against Hermes v0.22.2 (36 tools discovered) and OpenClaw
  2026.10.6.
- **Scoped agent keys**: `POST /api/v1/agent/keys` mints a one-time `read`/`trade`/`admin`
  key, stored only as a peppered SHA-256 hash; `GET` lists metadata, `DELETE` revokes.
  Middleware enforces least privilege per route, and `EZCHAIN_REQUIRE_AUTH_FOR_READS=true`
  closes the read surface for hosted deployments. Migration `0008_api_keys`.

- **Native desktop app (Linux verified)**: an Electron shell with a frozen FastAPI
  sidecar on embedded SQLite and a traced Next.js server, both bound to private loopback
  ports. A native window replaces the browser tab; closing hides to the tray, a watchdog
  restarts crashed sidecars, and the database/logs/token live under
  `~/.config/ezCHAIN/`. AppImage and `.deb` build with `bash tools/build-desktop.sh
  --package`. macOS and Windows targets are configured but not built. See
  `docs/DESKTOP.md` and `docs/evidence/desktop/linux-app.md`. No keys, signing or live
  execution are added.

- **Paper account read models**: `GET /api/v1/paper/ledger`, `/performance`,
  `/attribution`, `/limits`, `/alerts` and `/export/{orders,fills,ledger}.csv` project the
  append-only ledger into UI-facing read models (cash, equity, realized/unrealized PnL,
  per-token attribution, risk limits, derived operator alerts and CSV exports). Every
  number is server-computed; an unknown mark is `null`, and no endpoint mutates state.
- **Graph strategies are live on the PAPER path**: `GET /api/v1/graph/catalogue`,
  `POST /api/v1/graph/validate`, `POST /api/v1/graph/dry-run`,
  `POST /api/v1/backtests` (+ list/read), and
  `GET /api/v1/strategies/{id}/performance`. The scheduler builds a real `EvalContext`
  (live snapshot, closed candles, ledger position/account, run-log variables) and maps
  `GraphDecision` onto the existing order path; buy uses `amount_usd`, sell uses
  `sell_fraction × position value` and never oversells. Backtests are immutable rows in
  the new `backtests` table (`0006_backtests`, forward-only). Validation and dry-run
  keep unknown values `null`; the dry run never places an order. Live signing/execution
  still does not exist.
- **Operator UI routes**: every sidebar destination now renders a real surface backed by
  the typed API clients or an explicit labelled FIXTURE/empty/error state. Adds
  performance, wallets, activity, reports (browser CSV), automations, signals, backtests
  status, risk with a persisted kill-switch control, derived alerts, chains/coverage,
  integrations, settings, security, developer, operations (health/ready/metrics/
  reconciliation), workspace and the state/component/mobile galleries. The placeholder
  catch-all route is removed; unknown routes return 404.

- `docs/evidence/p0-production/`: live market/paper/strategy/auth/API-down/restart and
  stress evidence for the PAPER production operations.
- `docs/PRODUCTION-READINESS.md`: verified vs unverified matrix, operator checklist and the
  release rule for the PAPER stack (398 Python + 36 Playwright tests, live auth-enabled
  smoke, stress suite, migrations).
- Stress harness bearer support via `EZCHAIN_STRESS_API_TOKEN`, with HTTPS/loopback
  enforcement and report redaction (#79).

- **PAPER Docker stack (issue #63)**: `apps/api/Dockerfile` and `apps/web/Dockerfile`
  build from the repository root as multi-stage, non-root images; the API image installs
  `requirements-runtime.txt` only (no pytest/ruff/mypy); `infra/compose.yaml`
  runs Postgres 16, a one-shot Alembic `migrate` job, the API, the web app and Caddy, with
  healthchecks, restart policies and resource budgets. Only Caddy publishes a port.
- Optional `observability` compose profile: Prometheus scrapes only the real
  `api:8000/metrics` endpoint; Grafana remains localhost-only and has no provisioned
  dashboards.
- `infra/compose.blueprint.yaml` records the future Redis/NATS/engine/executor/ingest/OTel
  services with their blocking reasons. They are profile-gated and not runnable.
- `Docker quick start` in `README.md`: required secrets, token generation, URLs, health
  endpoints and the internal-only OpenAPI UI.
- `docs/INFRA.md` now describes the actual PAPER topology, networks, migrations,
  health/metrics, backups and restore; `docs/RUNBOOK.md` adds migrate/upgrade/rollback,
  401, health and compose operations.
- **Alembic baseline** (`0001_baseline`) for the current paper schema, generated against a
  fresh Postgres database. `ezchain-api migrate` / `python -m ezchain_api.migrate` runs
  `alembic upgrade head`; `/readyz` returns 503 unless the database is reachable and
  `alembic_version` equals the script head. A test rejects destructive `upgrade()` bodies
  (CONTRACT I8).
- **Mutation auth**: every non-`GET`/`HEAD`/`OPTIONS` request under `/api/` requires
  `Authorization: Bearer $EZCHAIN_API_TOKEN`; reads stay open. Production refuses to boot
  with an unsafe config (no token, short token, `auto_create_schema`, fixture mode, `*`
  CORS, default/passwordless database URL). The Next server proxies browser `/api/*` calls
  and injects the token server-side, so it never reaches the browser.
- **Observability**: one JSON access log line per request with a safe/echoed
  `X-Request-ID`, route template and duration (no tokens, bodies or query values);
  `/healthz` liveness, `/readyz` readiness, and a dependency-free Prometheus `/metrics`
  with bounded labels (`ezchain_http_requests_total`, request-duration histogram,
  paper-order/fill counters, `ezchain_up`).
- **P0 live paper slice**: FastAPI edge, Postgres schema, `packages/core-py` domain and
  append-only ledger, DexScreener/Jupiter adapters, and an inline strategy scheduler.
- Live multi-chain market browsing with a 25-chain registry, source and `observed_at` on
  every row, bounded cache/rate limit and fail-closed normalization.
- Jupiter paper quotes with a mandatory full-size sell route; a `paper_mark` for
  non-Solana chains, capped and labelled as not executable.
- Idempotent paper orders, fills, positions, cash/realized/unrealized PnL and risk limits,
  all derived from append-only ledger entries.
- Continuous all-chain market observation ("market eyes"): a bounded inline scan discovers
  across every registered chain, persists observations, records `no_data`/`stale`/`error`
  instead of zero, and exposes `/api/v1/markets/overview` and `/api/v1/markets/stored`.
- All-chain market browsing now fans out one token lookup per registered chain (seeds first,
  at most 12 hints, four in flight) because `/latest/dex/tokens` caps each response at 30
  pairs. Rows are ranked within each chain and round-robined before the limit, so every
  data-bearing chain is represented instead of the global top few; the response carries
  optional per-chain `coverage` and `ordered_by`. Verified seeds were added for Blast,
  Mantle, Linea, Scroll and zkSync.
- User-defined declarative strategies (momentum, dip, DCA, threshold), pause/resume,
  run-now, interval gate and an append-only run log.
- Read-only paper reconciliation (`GET /api/v1/paper/reconciliation`): journal balance,
  cash replay, position sign, fill/order integrity and `quote_kind` counts, all derived
  from persisted rows.
- Persisted strategy-scoped operator kill switch (`/api/v1/system/kill-switch`); armed
  switches block strategy-originated orders, log a hold, and keep manual paper orders
  available.
- Live web surfaces: markets, token page with paper ticket, positions and strategies;
  `EZCHAIN_WEB_MODE=fixture` keeps Playwright deterministic and labelled.
- Python setup/dev scripts (`tools/setup-python.sh`, `tools/dev.sh`) and pytest coverage
  for adapters, paper accounting, strategies and quotes.
- `apps/web` M1 scaffold: Next.js shell, dashboard, positions page with four tile states,
  markets screener and token page with a fail-closed quote panel.
- Generated design tokens (`design/tokens.json` → `design/tokens.css` and
  `packages/ui/src/tokens.css`) and the `tools/build-tokens.mjs` gate.
- Playwright smoke/screenshot checks for dashboard, positions and markets at 1440 and
  390 px, plus token page desktop, mobile and fail-closed screenshots.
- Markets render evidence: wireframe-vs-implementation and mobile screenshots under
  `docs/evidence/m1-ui/`; token page evidence adds desktop, mobile and fail-closed shots.
- Project scaffold: product, architecture, domain model, chain adapter contract,
  strategy SDK, security, infra and PR workflow docs.
- 31-page low-fidelity wireframe in `design/wireframe/` with a manifest and gates.
- Performance screen: attribution, R-multiple distribution, benchmark and cost drag.
- Deployment blueprint: compose services, Caddy edge, Prometheus alerts, OTel collector.
- CI gates: agent stubs, design tokens, wireframe integrity, markdown links, secret scan,
  and the pnpm lint/typecheck/build/test runtime gate.
- PAPER-only stress harness (`tools/stress/`): smoke, soak, idempotency and chaos modes,
  pure invariant checks for orders/fills/positions/account, latency budgets, JSON+Markdown
  evidence, and opt-in deduplicated GitHub issue filing by failure fingerprint.
- `tools/stress/run.sh` (venv-aware), `tools/stress/README.md` with cron/launchd examples,
  and pytest coverage for the pure checkers and issue dedupe.
- **Server-ranked market movers.** `GET /api/v1/signals/movers` answers one
  window (`h1`, `h24`) and one direction (`up`, `down`) with the movers the API ranked:
  one row per token, ordered by the window's own price change, behind a liquidity floor
  and a freshness budget. Rows with an unknown change, an unknown liquidity or a stale
  mark are excluded and counted under their own reason instead of being dropped
  silently, and every returned row carries its source, observation time and age. An
  empty answer carries a reason code; no mover is invented to fill the table. The
  Signals page reads this model instead of sorting the market list in the browser.

### Changed

- **Operational endpoints require the operator token off loopback.** `/metrics` and
  `/health` answer a loopback client without a credential and return 401 to every other
  client; the Caddy edge no longer publishes `/metrics`. `/docs`, `/redoc` and
  `/openapi.json` return 404 off loopback, and `/api/v1/agent/state` requires a `read`
  principal even when reads are open. A new `EZCHAIN_TRUSTED_PROXY_CIDRS` setting lists
  reverse proxies whose `X-Forwarded-For` may be read; it is empty by default.
- README/roadmap now state the product is **alpha and paper-only**, explicitly not ready
  for real-money trading, and gate official Hermes/OpenClaw marketplace listings on
  real-mode readiness. README adds a contributor welcome section.
- Execution mode is server-derived. `EZCHAIN_EXECUTION_MODE=live` is refused at boot
  until the executor ships; no API or MCP call accepts a mode argument.
- **Discovery-first shell**: the sidebar now leads with Browse and Signals, keeps
  Dashboard/Positions/Performance in Overview, keeps Automate as a secondary sidebar group
  and under More on mobile, and links to `/more` for reference galleries. The 240px
  sidebar stays expanded at desktop widths and has a persisted user toggle to a 72px
  labelled rail; the topbar search names markets, and `All chains` opens the market
  browser. Heavy routes ship loading skeletons and the root view has an honest error
  boundary; error-boundary rendering is manually verified, not CI-covered.
- Caddy now sends `/api/*` to `web:3000` so the Next.js route handler injects the bearer
  token; `/healthz`, `/readyz` and `/metrics` go to `api:8000`, and everything else is the
  web app. The edge listens on `:8080` instead of matching `localhost`.
- `infra/.env.example` now matches the real app: psycopg URL, `EZCHAIN_ENVIRONMENT=prod`,
  `EZCHAIN_AUTO_CREATE_SCHEMA=false`, `EZCHAIN_AUTH_ENABLED=true`, `EZCHAIN_API_TOKEN`,
  container API URL and the inline engine/market flags. Future-only keys are commented.
- Prometheus scrapes only `api:8000` and itself. Alerts now reference metrics the API
  exposes (`ezchain_up`, `ezchain_http_requests_total`, `ezchain_paper_orders_total`);
  executor, queue, breaker, drift and backup alerts were removed.
- `README.md` and infra docs now describe the runnable PAPER stack instead of calling
  Compose a non-runnable blueprint.
- `README.md` quick start now runs the Alembic baseline and documents bearer auth;
  `CONTRIBUTING.md` no longer claims the Python services do not exist. `docs/INFRA.md`
  separates runnable local pieces from the unverified Docker blueprint, and
  `docs/RUNBOOK.md` documents migrate-on-deploy, legacy `stamp 0001_baseline` then upgrade, probe semantics and
  401 triage.
- `README.md`, `ROADMAP.md`, `docs/ARCHITECTURE.md` and `docs/PLAN-LIVE-PAPER.md` now
  describe the P0 live paper slice; the web defaults to live and tests set `fixture`.
- `docs/CHAINS.md` now presents a **target** support matrix with a `Verified today`
  column; Solana is marked paper-verified, not live-verified.
- `docs/PRODUCT.md` labels the v1 scope table as a target, not shipped.

### Fixed

- **Paper order idempotency is bound to the request payload (PAPER).** A key is now
  stored with a canonical SHA-256 of the chain, token, side, amount and strategy it
  names. Reusing the key with the same payload returns the original order (`200`),
  a first create returns `201`, and a changed payload returns
  `409 IDEMPOTENCY_CONFLICT` instead of silently returning the first order. Amounts
  are compared canonically, so `50`, `50.0` and `50.00` are one payload. Strategy
  keys also carry the action and resolved amount, so two different decisions in one
  window cannot collapse into one order. Migration `0011_idempotency_fingerprint`
  adds the nullable column; rows written before it derive and store their hash on
  first reuse, so an identical retry still replays and a changed payload conflicts.
- **Reduce-only exits are no longer blocked by entry-only risk gates** (#109):
  a sell flagged `reduce_only`, or one sized to the whole position, now reaches an
  executable sell quote even when the market feed has no mark, thin or unknown
  liquidity, or no pair. Exit integrity still fails closed: an open position, a fresh
  quote, a positive executable price, a route and a quantity no larger than the position
  are required. Sell sizing is quantity first; a sell with no route is recorded as
  `EXIT_NO_ROUTE` with the router's evidence. Protection arms send the exact token
  quantity and the reduce-only flag, so a stop or ladder level cannot be capped by an
  entry gate.
- **Account and strategy performance share one PnL projector.** Account performance
  replayed ledger groups while strategy performance walked its own fills, so a buy fee
  and a partial close produced different realized and unrealized numbers. Both now
  project the same ledger lines, with the strategy's attributed group ids as the scope.
  The strategy response also reports its `market_value_usd` (equity contribution),
  `complete` and `marks_unknown`; an unknown mark stays null on both surfaces. A buy fee
  is realized when the ledger pays it, and win rate uses the same decided-close
  denominator on both responses.
- **A market mark race can no longer fail a valid paper order** (#112). Order pricing and
  the market monitor now write the latest observation for a token with one atomic upsert
  on the existing `(chain, token)` unique index (SQLite and Postgres), so two writers at
  once converge on one row instead of one raising an integrity error. A stored mark only
  moves forward: a delayed observation cannot replace a newer one, and an observation
  dated ahead of the writer's clock cannot freeze it. A single refused row no longer drops
  the rest of a monitor scan batch.
- **Paper equity fails closed on unknown marks** (#96, #108):
  `GET /api/v1/paper/account` now returns `equity_usd: null` and `unrealized_usd: null`
  when any open position has no mark, and exposes `complete`, `marks_unknown`,
  `known_market_value_usd`, `unpriced_positions` plus server-computed `by_chain` and
  `by_asset` composition. The dashboard shows the composition, with cash as a labelled USD
  settlement row, instead of an understated single total; graph contexts receive null
  equity rather than a partial sum.
- The market monitor now refreshes marks for open paper positions after each scan,
  including quote-side tokens that discovery would not store, bounded by
  `max_open_positions` (#88).
- Playwright fails fast with an actionable message when a `next dev` server is running
  on :3000, instead of corrupting the shared `apps/web/.next` manifest and producing a
  flaky 500. The `.next-test` approach was reverted because Next rewrites tracked type
  files for the active build directory (#36).
- Playwright uses a separate `.next-test` build directory, so a running `next dev` on
  :3000 cannot corrupt the gate server on :3100. The gate now stays green with a dev
  server active (#36).
- Market sorting is None-safe for every `sort` mode: missing `volume`/`liquidity` stays
  `null` on the row, sorts last, and is never displayed as zero.
- Market coverage now distinguishes `error` from a successful empty `no_pairs`, and the
  overview totals no longer count a chain as both covered and errored. Chains the all-chain
  fan-out already attempted are not re-probed, keeping a successful monitor scan within its
  request budget.
- Chain staleness now includes the last scan duration, so a slow pass does not make every
  chain look stale before the next pass.
- The documented pair-age gate is enforced for paper buys: `PAIR_AGE_UNKNOWN` when the
  source omits creation time and `PAIR_TOO_YOUNG` below `EZCHAIN_MIN_PAIR_AGE_MINUTES`
  (default 10). Sells stay exempt so an exit is never blocked (#67).
- Strategy runs now record `hold` / `ORDER_REJECTED` when the paper order service rejects
  an order, instead of claiming a buy or sell that never filled. The rejected order id and
  reason stay in the run inputs for diagnosis (#71).
- The manual paper-order route no longer accepts strategy attribution; the order service
  also rejects strategy-attributed orders while the kill switch is armed, before quoting.
- Concurrent first boots no longer fail when both processes seed the kill-switch row; the
  loser rolls back and reads the winner.
- Reconciliation cash is checked against genesis plus persisted fill cash flow instead of
  a tautological replay; disarming the kill switch now requires a reason (422).
- The dashboard labels every fixture-driven panel `FIXTURE` in live mode; the Risk panel
  no longer says "live checks" (#76).
- The web strategy builder's DCA and dip vocabularies now match the server validator:
  DCA no longer offers take-profit/stop-loss and dip no longer offers `exit_change_pct`.
  Creating either strategy no longer returns `STRATEGY_PARAM_UNKNOWN` (#73).
- Paper sells are no longer blocked by order-size, paper-mark-size or daily-loss limits;
  those limits gate new risk, not exits. Fail-closed price/liquidity/route/freshness gates
  still apply to sells (#65, PR #66).
- Position-tile charts no longer widen the page at 390 px (#23).
- Deleting a strategy now removes its `strategy_runs` rows and the strategy in one
  transaction instead of failing on the foreign key; historical orders keep their
  `strategy_id` string. Pause instead of deleting when the decision log must be retained.
- Concurrent run-now and scheduler evaluation of one strategy is resolved by the existing
  order idempotency key: at most one order and one fill per window.
- Dashboard bank activity and the position-tile peak now agree with the token fixture;
  a cross-page test guards the constants (#38).
- DexScreener token lookup now resolves the requested side (base or quote) and derives a
  quote-side USD price from `priceNative`, or fails closed.
- Paper sells are no longer denied by position-size limits; buy/sell journals balance, and
  no-route quote errors map to `NO_SELL_ROUTE` (409).
- API timestamps are normalized to UTC; paper positions without a mark stay unknown.
- Deleting a strategy now removes its run rows in the same transaction, so a strategy with
  history no longer fails with a 500 (found by the stress harness cleanup check).

### Removed

- Removed unused `paper_mark_fee_bps` and the dead `cents_to_usd`/`usd_to_cents`
  helpers. Money is documented as decimal USD strings and exact `NUMERIC` storage, not
  integer cents (#86).
- Decorative default-stack services that no code used: TimescaleDB (replaced by plain
  Postgres 16), Redis, NATS, the standalone engine, executor and ingest. They are either
  inline in the API today or not built; the intended future shape is in
  `infra/compose.blueprint.yaml`.
- `asyncpg`, bare `DATABASE_URL`, `EZCHAIN_ENV=local`, and the active NATS/Redis/vault
  keys from `infra/.env.example`. The only database URL is the psycopg one.

## [0.0.0] — 2026-10-20

Initial design drop. No runtime code.
