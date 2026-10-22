# M1 UI evidence

Rendered with Chrome at fixed viewports on 2026-10-22.

| File | What |
|---|---|
| `dashboard-wireframe-vs-implementation.png` | `design/wireframe/03-dashboard.html` (left) vs `apps/web` at 1440 px (right) |
| `dashboard-mobile-390.png` | `apps/web` dashboard at 390 × 844, full page, with the fixed bottom nav |
| `positions-wireframe-vs-implementation.png` | `design/wireframe/04-positions.html` (left) vs `apps/web/positions` at 1440 px (right) |
| `positions-mobile-390.png` | `apps/web/positions` at 390 × 844, full page |
| `markets-wireframe-vs-implementation.png` | `design/wireframe/06-markets.html` (left) vs `apps/web/markets` at 1440 px (right) |
| `markets-mobile-390.png` | `apps/web/markets` at 390 × 844, full page |

The comparison is structural, not pixel-perfect: the wireframe is the low-fidelity
source, and the implementation uses self-hosted Inter / IBM Plex Mono, a real shell and
fixture data. Deliberate visual deltas are recorded in
`design/OWNER-APPROVED-DELTAS.md`.

Regenerate with:

```bash
pnpm --filter @ezchain/web test
# screenshots land in apps/web/.shots/
```
