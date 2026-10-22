# VISUAL TESTING — rendered evidence

Source comparison is not evidence for a UI change. Render the page, capture it at fixed
viewports, and compare it with the wireframe or the approved visual.

## Local workflow

```bash
pnpm install
pnpm --filter @ezchain/web test       # Playwright smoke + screenshots
```

The Playwright config starts the app on `127.0.0.1:3100`, uses installed Chrome when
present and bundled Chromium otherwise (override with `EZCHAIN_PW_CHANNEL`), and writes
screenshots to `apps/web/.shots/` (gitignored):

- `.shots/dashboard-1440.png` — desktop, full page
- `.shots/dashboard-390.png` — mobile, full page
- `.shots/positions-1440.png` — desktop, full page
- `.shots/markets-1440.png` — desktop, full page
- `.shots/markets-390.png` — mobile, full page
- `.shots/token-1440.png` — desktop, full page
- `.shots/token-scam-1440.png` — SCAM fail-closed variant
- `.shots/token-390.png` — mobile, full page

The committed comparison evidence lives in `docs/evidence/m1-ui/` (dashboard, positions,
markets and the token page, including the fail-closed state).

For a manual comparison, serve the wireframe and the app side by side:

```bash
npx serve design/wireframe -l 3001   # http://localhost:3001/03-dashboard.html
pnpm --filter @ezchain/web dev       # http://localhost:3000
```

## What the gate checks

1. The route returns 200 and the expected page heading is visible.
2. No external HTTP(S) requests are made by the rendered page.
3. The mobile bottom navigation is present at 390 px.
4. Screenshots are produced at the fixed viewports.

It does **not** pixel-diff the wireframe: the wireframe is structural and uses different
markup. A human compares the rendered result with the design, and records deliberate
deviations in `design/OWNER-APPROVED-DELTAS.md`.

## Evidence in PRs

Attach the relevant screenshots to the PR. Commit a reviewed comparison image only when
it is release evidence; do not commit scratch screenshots or `_tmp_*` files.
