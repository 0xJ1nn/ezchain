# apps/web — ezCHAIN UI

Next.js 15 App Router UI for the ezCHAIN workstation. This is the M1 scaffold: the
shell, dashboard, positions page, markets screener and token page are implemented
against the wireframe. Every other route renders an honest "not implemented yet" page
until its scoped PR lands.

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

## Rules

- The UI formats; it never computes PnL, exposure or ledger math. Fixtures stand in for
  the server `PositionView` until `apps/api` exists.
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
