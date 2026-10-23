# AGENTS.md — AI & developer instructions

> **Maintainer workflow:** if the maintainer invokes **“leader go”** or **“leader mode”**,
> read the maintainer's local, untracked orchestration brief if one is present in the
> working tree. If it is absent, ask the maintainer; do not invent a process. Internal
> orchestration and review material is intentionally not part of this public repository.
>
> **Canonical engineering rules:** [`project.md`](project.md) is the single source of truth
> for how ezCHAIN is developed. Read it before every meaningful task.
>
> This file is the operational quick reference for humans and AI agents. When anything
> here conflicts with `project.md`, `project.md` wins. When either conflicts with
> [`docs/CONTRACT.md`](docs/CONTRACT.md) invariants or an explicit maintainer instruction,
> those win.

`CLAUDE.md`, `.github/copilot-instructions.md`, `.codex/instructions.md`,
`.opencode/instructions.md` and `.cursor/rules/ezchain.mdc` are **generated pointers** to
this file (`node tools/sync-agent-stubs.mjs`). Never hand-edit them.

---

## Priority of authority

1. Explicit human instruction (the maintainer says so).
2. Project architecture and safety rules, including `docs/CONTRACT.md`.
3. Canonical policy: [`project.md`](project.md).
4. Task-specific instructions.
5. Agent preference.

An AI profile may never override an explicit human instruction. If two instructions
conflict, stop and ask.

---

## First thing every session — RECONNAISSANCE

Before changing anything for a non-trivial task, **stop and inspect**:

- [`project.md`](project.md) — engineering governance
- [`docs/CONTRACT.md`](docs/CONTRACT.md) — normative invariants
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — deployables, boundaries, data flow
- [`README.md`](README.md) — what the project is
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — relevant ADRs
- maintainer-local task/handoff notes (deliberately not part of this public repository)
- `git status`, current branch, relevant source and tests

**The code is truth. Documentation can be stale. Read the code. If they disagree, fix the
doc in the same PR.**

---

## Platform invariants — read before touching a money path

The full list is normative in [`docs/CONTRACT.md`](docs/CONTRACT.md). The ones that catch
agents most often:

1. **The ledger is the only accountant.** Cash, size, realized PnL and fees exist only as
   append-only `ledger_entries`. No mutable balance scalar. Position state and journal
   commit atomically.
2. **One execution path.** Paper, testnet and live share
   `Intent → RiskDecision → Quote → Order → Fill → Journal`. Only the venue adapter and
   custodian differ.
3. **Fail closed on money.** Unknown token risk, missing quote, stale oracle, unreachable
   risk service → no new order. Unknown is never zero.
4. **Keys live only in `services/executor`.** No key material in web, API responses, logs,
   env dumps, fixtures, traces or strategy plugins.
5. **No strategy code in this repository.** Strategies are external plugins. Examples
   live in a separate repository.
6. **Chains are capabilities, not branches.** Core code branches on capability flags, never
   on `chain_id` or a chain-name literal.
7. **Every number has one home.** Config is schema-validated; the UI reads it; no duplicate
   defaults.
8. **Migrations are forward-only and never destructive.** A data reset is an operator
   action, documented and audited.
9. **Replay or it didn't happen.** Any rule that can move money is replayable from
   persisted inputs. If it cannot be measured, it cannot gate money.
10. **One position model, one tile.** `PositionView` is computed server-side. The UI
    formats; it never recomputes PnL.

Before proposing a change to a money path or a service boundary, state which invariant it
touches. If it touches one, it needs an ADR in `docs/DECISIONS.md` and explicit maintainer
approval.

---

## Path → responsibility

| Path | Owns | Never contains |
|---|---|---|
| `apps/web` | UI, routing, presentation | business rules, key material, PnL math |
| `apps/api` | HTTP/WS edge, auth, validation, read models | order execution, accounting math |
| `services/engine` | strategy runtime, automation scheduler | signing, direct DB writes outside repositories |
| `services/executor` | signing, broadcast, fill confirmation | strategy decisions, UI data |
| `services/ingest` | chain/market data acquisition, normalization | trading decisions |
| `packages/core-py` | domain types, money, ledger, risk policy | I/O, HTTP, framework code |
| `packages/sdk-py` | strategy + automation plugin contract | platform internals |
| `packages/adapters/*` | one chain or venue per package | cross-adapter conditionals |
| `design/` | wireframe, tokens, design source of truth | runtime code |
| `infra/` | deployment contract, config schema | secrets |

---

## Scope & discipline rules (short form)

- **ONE task at a time.** One problem → one plan → one branch → one PR.
- **SCOPE LOCK.** Define what is and is not in scope before starting.
- **NO unrelated refactoring.** Fix what was asked. Do not touch other systems.
- **Investigate first, code second.** Read-only pass before editing.
- **Smallest safe change.** No speculative abstractions, no premature optimization.
- **Existing functionality is sacred.** Do not change working interfaces without
  understanding who depends on them.
- **Boundaries are hard.** The executor is the only key holder; the ledger is the only
  accountant; the web app formats only.
- **Prefer deleting to adding.** Dead code, stale docs, unused flags and superseded paths
  are removed in the PR that makes them dead — no "keep for reference".

Ask before: deleting user data, changing license, adding a dependency with a network or
binary component, altering an invariant, or rewriting history.

---

## Testing discipline

- New code ships with a failing-first test (`pytest`, `vitest`, Playwright).
- A bug that can be reproduced gets a regression test.
- Money math and ledger invariants need property tests, not just examples.
- Adapters must pass the shared adapter contract suite; strategies the SDK conformance
  suite.
- Explain **why** a test changed. Never edit a test to green without a reason in the PR.
- `tools/ci.sh` is the gate: agent stubs, wireframe integrity, docs links, secret scan,
  and runtime gates when present. A red gate is never merged.
- **GitHub Actions is not available on this account.** Run the gate locally and quote the
  result in the PR. Never add a workflow that cannot run.

---

## Git and PR workflow

- `main` is always releasable; never develop on it.
- Branch from fresh `origin/main`: `feat/…`, `fix/…`, `docs/…`, `design/…`,
  `refactor/…`, `test/…`, `chore/…`, `release/…`. Unfinished work goes on a pushed
  `wip/…` branch.
- Parallel agents use separate worktrees and branches; one active implementer per branch.
- Conventional Commits, signed off (`git commit -s`), one logical change per commit.
- Every change lands by PR, even solo. The PR body follows the template: what/why, scope,
  risk, verification commands and their observed results, screenshots for UI changes.
- Squash-merge. The squash subject is the conventional commit; the body keeps the PR
  summary.
- Never force-push `main`. Never commit generated files by hand.

---

## Release discipline

- Version follows [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.
- `CHANGELOG.md` records user-facing changes under `[Unreleased]`.
- `docs/DECISIONS.md` records architecture decisions.
- Do not release from an unreviewed branch.
- What you could not verify, **do not claim works** — say "not verified".

---

## Design and generated artifacts

- `design/wireframe/` is the structural source of truth: layout, content, states,
  interactions. Visual styling is downstream (`design/BRIEF.md`).
- A wireframe change must keep `node tools/check-wireframe.mjs` green: every page in
  `design/wireframe/manifest.json` exists, every internal link resolves, no external
  requests, no secrets.
- Generated files (agent stubs, schemas, wireframe manifest indexes) are rebuilt, never
  edited.
- Visual PRs include rendered screenshots; comparing source code alone is not evidence.
  Deliberate deviations go in `design/OWNER-APPROVED-DELTAS.md` with a reason.

---

## What AI must NOT do silently

- Make major architectural, product, UX, compatibility, licensing, dependency or scope
  decisions.
- Weaken an invariant in `docs/CONTRACT.md`.
- Put key material anywhere outside `services/executor`.
- Add strategy code to this repository.
- Branch on chain identity outside `packages/adapters/`.
- Add a package format that can execute code, fetch URLs or escape its directory.
- Add telemetry, analytics, remote calls or "helpful" fallbacks.
- Copy external code without license/provenance checks.
- Change generated files (change the source instead).
- Execute destructive commands (`rm`, `git reset --hard`, force push) without explaining
  first.
- Rewrite files, configs or lockfiles unrelated to the task.
- Remove copyright notices or modify licenses.
- Override human instructions.
- Commit scratch agent tooling — screenshot drivers, input injectors, capture helpers,
  probe scripts go in `/tmp` only, are wiped when the task ends, and are never committed;
  only real permanent tests live in `test/`.
- Push AI-generated doc clutter — session notes, status/summary/plan files — without
  explicit maintainer approval. Verify every push file-by-file (`git status --short`,
  `git diff --stat`, commit list) and list exact files pushed.

---

## Teaching rule

The maintainer is actively learning professional software engineering. For every important
technical decision:

1. Explain the problem in simple language.
2. Explain what the current system does.
3. Explain the proposed change.
4. Explain **why** it is being proposed.
5. Explain what could break.
6. Explain alternatives.
7. Explain how we will test it.
8. Ask for direction when the decision is significant.

**The objective: improve ezCHAIN while making the maintainer progressively better at it.**

---

## Model honesty

- Never fabricate test results, build results, benchmarks, compatibility claims or code you
  did not inspect.
- When uncertain, say so — "I found two plausible interpretations" rather than guessing.
- Two AIs agreeing does **not** mean it is correct — evidence comes from tests and runtime
  behavior.
- If an AI starts oscillating or patching repeatedly without converging, **STOP**, revert to
  known-good, and restart with investigation.
- Claims name their data population and harness. Paper results are labelled `PAPER`;
  backtests name fees, slippage and fill model.

---

## Cross-tool safety

One active implementer per branch. Do not let multiple agents (OpenCode, Claude, Codex,
Hermes) edit the same files simultaneously. Use worktrees and separate branches for
parallel work.

---

## Verification without vision

```bash
node tools/check-wireframe.mjs     # wireframe page/link/asset integrity
node tools/sync-agent-stubs.mjs --check
node tools/check-links.mjs
node tools/check-secrets.mjs
bash tools/ci.sh                   # full local gate
```

Report the command and its exit status in the PR. If you did not run it, say so.

---

> **Remember:** "How much reliable progress can we make today without damaging what
> already works?"
