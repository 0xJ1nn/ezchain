# apps/web — ezCHAIN UI

Next.js 15 App Router UI for the ezCHAIN workstation. Every route in the sidebar is a
real surface: it either reads a live API view model, renders an explicit FIXTURE
preview, or states the exact missing backend instead of showing a dead placeholder.
The shell, dashboard, positions, markets, token, strategy and system pages share the
same primitives and formatting rules.

## Run

```bash
pnpm install
pnpm --filter @ezchain/web dev
```

Open <http://localhost:3000>.

## Verify

```bash
pnpm --filter @ezchain/web lint
pnpm --filter @ezchain/web typecheck
pnpm --filter @ezchain/web test       # Playwright smoke + screenshots
```

Playwright uses the installed Chrome channel (`channel: "chrome"`). Screenshots are
written to `apps/web/.shots/` and are gitignored; a reviewed subset may be committed as
release evidence under `docs/evidence/`.

## Visual strategy builder

`/strategies/builder` (new) and `/strategies/[id]/builder` (edit) are the graph editor for
`kind: "graph"` strategies — see `docs/STRATEGY-GRAPH.md`. It edits one typed JSON document
(`ezchain.graph/1`) and never evaluates it:

- `lib/graph-doc.ts` — pure `toDocument` / `fromDocument` for the §1 shape (no React);
- `lib/graph-catalogue.ts` — catalogue lookups and the §2 pin rules;
- `lib/graph-fixtures.ts` — the §5 catalogue, the §6 local validator and deterministic
  FIXTURE dry-run/backtest results;
- `lib/graph-api.ts` — the §9 client. Reads switch on `EZCHAIN_WEB_MODE`; **writes never
  do** (`POST/PATCH /api/v1/strategies` always reaches the API).

The only new dependency is `@xyflow/react` (MIT, pinned exactly, imported locally — no CDN
and no external requests; a Playwright test asserts that). Below 1024 px the page shows a
read-only graph summary instead of a squeezed canvas.

## Rules

- The UI formats; it never computes PnL, exposure or ledger math. Live pages read server
  view models; fixture pages are labelled `FIXTURE` on every panel.
- No silent fixture fallback: a failed live read renders its API reason code.
- Pages whose backend does not exist yet (live wallets/custody, notification delivery,
  team features) say so and link to the contract that will define them. Backtests are
  served by `/api/v1/backtests` and render only immutable `BACKTEST` rows.
- `design/wireframe/` is the structural source of truth. Visual deviations are recorded
  in `design/OWNER-APPROVED-DELTAS.md`.
- No external runtime or build-time font requests. Inter and IBM Plex Mono are vendored
  in `app/fonts/` with their SIL OFL 1.1 licenses and loaded with `next/font/local`.
- `NEXT_TELEMETRY_DISABLED=1` is set in the scripts; do not re-enable telemetry.

## Layout

```
app/            routes, layout, global CSS
components/     shell, primitives, chart frames
lib/            typed fixtures (server view models)
e2e/            Playwright smoke and screenshot checks
```
