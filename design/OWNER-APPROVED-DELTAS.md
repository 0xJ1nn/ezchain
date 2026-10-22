# Owner-approved design deltas

Every deliberate deviation from `design/wireframe/` in the visual pass is recorded here
with a reason. Format:

| Date | Screen | Delta | Why | Approved by |
|---|---|---|---|---|
| 2026-10-22 | All | Wireframe banner replaced by a `Fixture preview` banner; topbar tag reads `Workstation` | This is the app, not the wireframe. The fixture banner is the honest label for sample data with no API (CONTRACT I12). | maintainer instruction (M1 scope) |
| 2026-10-22 | All | Provisional light tokens derived from the wireframe palette | M1 implements structure first; the dark `Instrument` visual pass in `design/BRIEF.md` is still pending. | maintainer instruction (M1 scope) |
| 2026-10-22 | Dashboard | KPI totals reconciled to the position rows; Scamcoin shows the last executable value; attribution shares are net-PnL shares | The wireframe sample numbers contradicted each other. One model per number (CONTRACT I10) wins over copying the sample. | maintainer instruction (M1 scope) |
| 2026-10-22 | Positions | KPI band uses the tile sums: executable $220.50, unrealized −$129.20 | The wireframe KPI band ($1,284.20 / +$86.40) did not match its own four tiles. One model per number wins. | maintainer instruction (M1 scope) |
| 2026-10-22 | Markets | Secondary columns are hidden and the table scrolls horizontally at 390 px | Preserves column meaning and scannability; a card rewrite is a later design decision. | maintainer instruction (M1 scope) |
| 2026-10-22 | Markets | Filter, sort, search and watchlist controls are rendered disabled with "not wired in M1" | No fake interactivity and no client-side filtering of fixture data. Real wiring is tracked in #33. | maintainer instruction (M1 scope) |
| 2026-10-22 | Markets | Table footer says "Fixture order · page 1/1" and the page carries a fixture banner | The page must not imply a live feed or server-side pagination before the API exists. | maintainer instruction (M1 scope) |
| 2026-10-22 | Token | Bonk quote panel uses $164.20 · 4.7% after fill; banked $46.20; safety count 7; RUNG +200% | The wireframe contradicted itself ($118.00/$125.69, $46.28, safety 6, +125%). One model per number (I10). | maintainer instruction (M1 scope) |
| 2026-10-22 | Token | SCAM is a new fail-closed variant with NO SELL ROUTE, last exact quote $0.0041 retained, mark-based PnL labelled not executable | Demonstrates I3 without fabricating a route or a zero. | maintainer instruction (M1 scope) |

Rules:

- Structural changes (removed field, merged screen, new page) require a wireframe + docs
  update, not just a delta row.
- Visual changes (spacing, type, colour, elevation) do not need a row unless they change
  hierarchy or legibility.
