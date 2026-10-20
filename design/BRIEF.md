# BRAND / DESIGN BRIEF — ezCHAIN

Input for the visual design pass. The wireframe (`design/wireframe/`) fixes structure,
content and states; this file fixes intent. Read both before designing a screen.

## 1. What we are designing

A **professional trading workstation** that one person can run on their own machine. It
should feel like a serious instrument: calm, dense, legible under pressure, honest about
risk. Not a casino, not a dashboard template, not "crypto neon" by default.

Reference points (attitude, not copying): Bloomberg terminal density with modern type,
Linear's restraint, a hardware synth panel, an aircraft annunciator (status lights that
mean something).

Three words: **precise, calm, trustworthy.**

## 2. Non-negotiables

- Structure and information hierarchy come from the wireframe. Do not remove fields,
  merge screens, or add marketing sections.
- Mode (`PAPER`/`LIVE`) and the kill switch are always visible.
- Unknown is `—`, never `0`. Negative/positive use sign + colour + label, never colour
  alone.
- Every number is tabular and aligned. No rounded "hero" numbers in tables.
- The position tile is the signature component. Treat it as an instrument, not a card.
- Dark theme is primary; a light theme is a supported second skin, not an inverted filter.

## 3. Visual direction (2–3 options to explore, pick one)

The wireframe is grayscale on purpose. Propose one primary direction and one fallback:

**A. "Instrument" (recommended)** — near-black graphite surfaces, one warm accent for
action, semantic green/red/amber, hairlines instead of shadows, mono for numeric data,
humanist sans for labels. Dense, quiet, high contrast where it matters.

**B. "Paper terminal"** — light warm paper, ink text, one strong signal colour, thin
rules, almost no shadows. Calm, editorial, still dense.

**C. "Deep space"** — blue-black layered surfaces, a single cool accent, glow reserved
strictly for live/urgent states. If chosen, glow is a status signal, never decoration.

Avoid: purple gradient SaaS, glassmorphism on data, animated backgrounds, icon-only
navigation, oversized cards, drop shadows on every panel, rounded 24px everything.

## 4. Tokens to produce

Deliver a `design/tokens.json` + CSS variables:

| Group | Needed |
|---|---|
| Surface | canvas, raised, overlay, sunken, hairline, border-strong |
| Text | primary, secondary, muted, disabled, inverse |
| Semantic | success, danger, warning, info, neutral — each with bg/text/border |
| Accent | one primary + hover/active/focus; optional secondary for charts |
| Chart | grid, axis, series 1–6, entry line, stop line, trail line, level markers |
| Spacing | 4px base, 8/12/16/24/32/48 |
| Radius | 2 / 4 / 6 / 10 (no pill except tags) |
| Type | display, heading, body, label, mono; sizes 11–28; line-heights; tracking |
| Elevation | 0 hairline, 1 popover, 2 modal; no card shadows |
| Motion | 120/180/240 ms, ease-out; respect prefers-reduced-motion |

## 5. Typography

- Numeric/table: a mono or tabular-figure face (e.g. IBM Plex Mono, JetBrains Mono,
  Geist Mono). Must have slashed zero and clear 1/l/I.
- UI text: a neutral sans with a tall x-height (Inter, Geist, Söhne-like).
- Never use display fonts for data. Never letter-space numbers.
- Prices: 4–6 significant digits, trailing zeros trimmed, mono. Large PnL may use the
  sans at semibold, but only outside tables.

## 6. Component inventory (build these before pages)

1. App shell: sidebar, topbar, mode badge, sync chip, kill switch, command palette.
2. Buttons: primary, secondary, ghost, danger, destructive-armed, icon, split.
3. Inputs: text, search, numeric with unit, select, chain/token picker, slider, toggle,
   segmented control, date range.
4. Data display: stat, delta, badge/tag, sparkline, table (sortable, sticky, selectable),
   key-value list, progress/level track, hold-budget bar.
5. Chart frame: axes, crosshair, tooltip, level lines, entry/stop/trail/arm/bank/rungs,
   peak marker, stale tail, empty/no-data.
6. Position tile (all states) — the reference implementation.
7. Overlays: drawer, modal, confirm-armed, command palette, toast, inline banner.
8. Empty / loading (skeleton) / error (reason code) / offline / degraded / quarantined.
9. Chain badge, token avatar (deterministic per symbol), wallet avatar, strategy avatar.

## 7. Page priority for the visual pass

1. Position tile + Positions page (signature).
2. Dashboard.
3. Token page + quote panel.
4. Wallets / wallet detail.
5. Automation builder.
6. Signals + strategy detail.
7. Risk + alerts.
8. Everything else (ledger, reports, chains, integrations, settings, security, developer,
   operations, workspace, states, mobile, components).

## 8. Density and layout

- 12-column grid, 24px gutters, 1440px max content width; full-bleed charts allowed.
- Tiles: 3-up ≥1440, 2-up 1024–1439, 1-up <1024.
- Tables: 40px rows comfortable, 32px compact; 12px cell padding; sticky header.
- Sidebar 240px expanded / 64px rail; topbar 56px.
- Charts get at least 160px height in a tile; full page 320–480px.

## 9. Interaction and motion

- Hover reveals provenance and actions; nothing critical is hover-only.
- Live ticks flash subtly (background 120ms), never bounce.
- Destructive actions arm (label changes, 4s window) rather than opening a modal.
- Loading: skeletons in place, no layout shift. Errors: inline with reason + retry.
- Keyboard focus visible on every interactive element; focus ring uses the accent.

## 10. Accessibility

- WCAG AA contrast for text and critical icons; charts must be readable without colour
  (dashes, labels, patterns).
- Min target 32×32px (mobile 44×44).
- All charts have a text summary and a table alternative in the tile detail.
- Respect `prefers-reduced-motion`, `prefers-color-scheme`; zoom to 200% without loss.
- Screen reader landmarks: nav, main, complementary; the mode badge is announced.

## 11. Copy tone

Short, factual, no hype. Use the domain words: position, fill, intent, route, mark,
exposure, drawdown, paper, live. Errors explain what happened and what to do:
"Quote expired before signing. Nothing was sent. Retry?" Never blame the user, never
say "oops".

## 12. Deliverables

1. `design/visual/` — high-fidelity page designs for the priority list, desktop + mobile.
2. `design/tokens.json` + `tokens.css`.
3. Component library page (`29-components` upgraded to hi-fi).
4. A one-page rationale: direction, type, colour, density decisions, tradeoffs.
5. Redlines for the tile and the shell.

Do not implement runtime code in the design pass. Do not add screens without updating
`docs/WIREFRAME.md` and the manifest.
