# HANDOFF — M1 markets/token slice

**Date:** 2026-10-22
**Repo:** `/Users/jinultimate/Projects/ezchain`
**Remote:** `https://github.com/0xJ1nn/ezchain.git`
**Branch:** start from fresh `origin/main` (main is protected; no direct pushes)

## Read first

1. `project.md` — canonical rules
2. `AGENTS.md` — operational quick reference
3. `docs/CONTRACT.md` — invariants
4. `docs/ARCHITECTURE.md` — boundaries
5. `docs/TODO.md` — M1 checklist
6. `ROADMAP.md` — current/next/later
7. `docs/WIREFRAME.md` and `design/wireframe/` — structure
8. `docs/VISUAL-TESTING.md` — render evidence workflow
9. `git status`, `git log --oneline`, `bash tools/ci.sh`

## Current state

Merged and green:

- Governance (`project.md`, `AGENTS.md`, generated stubs).
- `apps/web` Next.js 15 App Router, fixture-driven; local Inter/IBM Plex Mono; no
  telemetry; no external runtime requests.
- Dashboard `/`; positions `/positions`; markets `/markets`; token
  `/tokens/solana/bonk` and fail-closed `/tokens/solana/scam`; mobile `/more`.
- Design tokens generated from `design/tokens.json`.
- Playwright gate: `bash tools/ci.sh` → 15 tests passed; `pnpm shots` → 15 passed.
- Evidence in `docs/evidence/m1-ui/`.
- Issue #9 closed. Issue #8 remains open for the rest of the tile states.

Important context:

- The app is a **fixture preview**. The top banner says `FIXTURE PREVIEW · SAMPLE DATA ·
  NO API · NO CHAIN CALLS`. Do not remove it.
- Markets/token controls are preview-only; filters/watchlist are disabled. Real wiring is
  #33, not part of the next page.
- Token quote panel fails closed: Bonk has a full-size sell check; SCAM has
  `NO SELL ROUTE`, retains the last exact quote `$0.0041`, and cannot confirm.
- Cross-page BONK constants are guarded by `apps/web/e2e/fixtures.spec.ts`.
- Mobile tables still scroll horizontally; the table→card conversion is a known M1 gap.
- A background docs pass merged `ROADMAP.md` and ADR-0014–0019 in #37. Issue #39 asks the
  maintainer to review those decisions; do not cite them as settled if the status changes.

## Next task

**#10 — wallets + wallet detail.**

- Read `design/wireframe/08-wallets.html` and `09-wallet-detail.html`.
- Routes: `/wallets`, `/wallets/[id]`.
- Fixtures: `apps/web/lib/wallet-fixtures.ts` (new).
- Components: `apps/web/components/wallet-card.tsx`, `wallet-detail` page-local.
- Reuse `AppShell active="wallets"`, `Panel`, `Badge`, `TokenAvatar`, `Stat`, `LiveDot`.
- No key material, no signing, no live balances; watch-only/hardware/exchange/local-vault
  variants are labels only.
- Add Playwright tests and screenshots at 1440/390; commit comparison evidence.
- Update `docs/TODO.md`, `CHANGELOG.md`, `.state/CURRENT_TASK.md`, `.state/PR_LOG.md`,
  `design/OWNER-APPROVED-DELTAS.md` if a data/design delta is needed.

After #10: #11 automations, #12 strategies/signals, #13 risk/alerts, #14 system pages,
#8 remaining tile states, #36 deterministic gate.

## Suggested swarm

Use separate worktrees and branches; one implementer per branch, reviewers read-only.

- Scout/planner: `opencode-go/glm-5.3-flash`
- Implementer: `opencode-go/deepseek-v4.1-flash`
- Independent implementer/fallback: `opencode-go/glm-5.3-flash`
- Visual reviewer: `opencode-go/minimax-m3.1-flash-free`
- Code/rules reviewer: `opencode-go/deepseek-v4.1-flash`
- Accessibility reviewer: `opencode-go/glm-5.3-flash`
- Data reviewer: `opencode-go/deepseek-v4.1-flash`

Lead owns shared files (`app/ui.css`, `components/ui.tsx`, `components/shell.tsx`,
`lib/format.ts`, docs) and integration. Implementers own only their route/fixtures/tests.

## Required loop

1. Branch from fresh `origin/main`.
2. Implement the smallest safe slice.
3. Run `bash tools/ci.sh` and `pnpm --filter @ezchain/web shots`.
4. Render 1440 and 390; compare side-by-side with the wireframe.
5. Spawn visual, code, accessibility and data reviewers.
6. Fix findings; rerun the gate.
7. Commit signed-off; open PR; squash-merge; close the issue only when acceptance is met.
8. Update `.state/PR_LOG.md`, `.state/CURRENT_TASK.md`, `docs/TODO.md` and `CHANGELOG.md`.

## Commands

```bash
git fetch origin
git checkout -b feat/web-wallets origin/main
pnpm install
bash tools/ci.sh
pnpm --filter @ezchain/web dev
npx serve design/wireframe -l 3001
pnpm --filter @ezchain/web shots
```

## Guardrails

- `main` is protected; PRs only. Never force-push main.
- No keys, seed phrases, API tokens, telemetry or remote calls.
- Unknown is `—`, never `0`.
- No client-side PnL/quote/accounting math.
- Do not edit generated token CSS or agent stubs by hand.
- Do not commit `.next`, `node_modules`, `.shots`, `test-results`, scratch scripts or
  session notes outside `.state/`.
