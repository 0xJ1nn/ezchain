# AGENTS.md — ezCHAIN engineering rules

Canonical rulebook for humans and AI agents. `CLAUDE.md`, `.github/copilot-instructions.md`,
`.codex/instructions.md`, `.opencode/instructions.md` and `.cursor/rules/ezchain.mdc` are
**generated pointers** to this file (`node tools/sync-agent-stubs.mjs`). Never hand-edit them.

## Priority of authority

1. Explicit human instruction (the maintainer says so).
2. `docs/CONTRACT.md` invariants — safety and architecture.
3. This file.
4. Task-specific instructions.
5. Agent preference.

An AI profile may never override an explicit human instruction. If two instructions
conflict, stop and ask.

## Reconnaissance before every non-trivial change

Read, in order: `docs/CONTRACT.md`, `docs/ARCHITECTURE.md`, the README of the module you
are touching, `docs/DECISIONS.md` (relevant ADRs), `git status`, and the tests that cover
the area. The code is truth; docs go stale. If they disagree, fix the doc in the same PR.

## Path → responsibility

| Path | Owns | Never contains |
|---|---|---|
| `apps/web` | UI, routing, presentation | business rules, key material |
| `apps/api` | HTTP/WS edge, auth, validation, read models | order execution, accounting math |
| `services/engine` | strategy runtime, automation scheduler | signing, direct DB writes outside repositories |
| `services/executor` | signing, broadcast, fill confirmation | strategy decisions, UI data |
| `services/ingest` | chain/market data acquisition, normalization | trading decisions |
| `packages/core-py` | domain types, money, ledger, risk policy | I/O, HTTP, framework code |
| `packages/sdk-py` | strategy + automation plugin contract | platform internals |
| `packages/adapters/*` | one chain or venue per package | cross-adapter conditionals |
| `design/` | wireframe, tokens, design source of truth | runtime code |
| `infra/` | deployment contract, config schema | secrets |

## Invariants (docs/CONTRACT.md is normative)

1. **Ledger authority.** No balance is stored as a mutable scalar. All cash is derived
   from append-only `ledger_entries`. A position update and its ledger entries commit in
   one transaction, or not at all.
2. **Fail closed on money.** Unknown token risk, missing quote, stale oracle, or an
   unreachable risk service means **no new order**. Unknown is never zero.
3. **One execution path.** Paper, testnet and live go through the same
   `ExecutionIntent → RiskDecision → Quote → Order → Fill` pipeline.
4. **Keys never leave the executor.** No private key in the web app, API responses,
   logs, env dumps, fixtures, or strategy plugins. Only `services/executor` decrypts.
5. **No strategy code in-tree.** Strategies are user-supplied plugins. Examples live in
   a separate repository.
6. **Chain logic is adapter logic.** Core code may branch on *capabilities*, never on a
   chain name.
7. **Config has one schema.** New knob = schema entry + default + docs + test. No dead
   constants, no duplicate defaults.
8. **Migrations are forward-only and never destructive.** No `wipe`/`clean_slate`
   migrations. A data reset is an operator action, documented and audited.
9. **Replayability.** Any rule that can move money must be replayable from persisted
   inputs. If it cannot be measured, it cannot gate money.
10. **One tile, one model.** Position data has exactly one view-model contract
    (`docs/WIREFRAME.md` §Position tile). No per-page recomputation of PnL.

## Scope discipline

- One task per branch. State the scope in the PR. Do not refactor unrelated code.
- Smallest safe change. Investigate before editing; do not guess file contents.
- Prefer deleting to adding. Dead code, stale docs, unused flags and superseded paths
  are removed in the PR that makes them dead — no "keep for reference".
- Ask before: deleting user data, changing license, adding a dependency with a network
  or binary component, altering an invariant, or rewriting history.

## Testing discipline

- Bug: reproduce with a failing test first, then fix. Keep the regression test.
- Money math and ledger invariants need property tests, not just examples.
- Adapters must pass the shared adapter contract suite; strategies the SDK conformance
  suite.
- Explain *why* a test changed. Never edit a test to green without a reason in the PR.
- CI (`tools/ci.sh`) is the gate: format, lint, typecheck, unit, invariants, docs links,
  wireframe integrity, secret scan. A red gate is never merged.

## Git and PR workflow

- `main` is always releasable; never develop on it.
- Branch from fresh `origin/main`: `feat/…`, `fix/…`, `docs/…`, `design/…`,
  `refactor/…`, `chore/…`, `release/…`. Unfinished work goes on a pushed `wip/…` branch.
- Conventional Commits, signed-off (`git commit -s`), one logical change per commit.
- Every change lands by PR, even solo. PR body follows the template: what/why, scope,
  risk, verification commands and their observed results.
- Squash-merge. The squash subject is the conventional commit; the body keeps the PR
  summary.
- Never force-push `main`. Never commit generated files by hand.

## Design and generated artifacts

- `design/wireframe/` is the structural source of truth: layout, content, states,
  interactions. Visual styling is downstream (`design/BRIEF.md`).
- A wireframe change must keep `node tools/check-wireframe.mjs` green: every page
  listed in `design/wireframe/manifest.json` exists, every internal link resolves, no
  external requests, no secrets.
- Generated files (agent stubs, wireframe manifest indexes) are rebuilt, never edited.

## What AI must not do silently

- Change architecture, dependencies, licensing, or product scope.
- Touch generated files, lockfiles, or unrelated config.
- Add telemetry, analytics, remote calls, or "helpful" fallbacks.
- Commit scratch scripts, screenshots, dumps, keys, or credentials.
- Fabricate results, benchmarks, or verification. "Tests pass" means you ran them this
  session and quote the command.
- Push AI status/planning docs to the repo; those belong in `.state/`.

## Verification without vision

```bash
node tools/check-wireframe.mjs     # wireframe page/link/asset integrity
node tools/sync-agent-stubs.mjs --check
bash tools/ci.sh                  # full local gate (once runtime exists)
```

Report the command and its exit status in the PR. If you did not run it, say so.

## Model honesty

Two AIs agreeing is not evidence. Cite files, commands or measurements. If a change
oscillates, revert to known-good and report the dead end rather than forcing a patch.
