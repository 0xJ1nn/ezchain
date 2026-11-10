# WIREFRAME — screen inventory, flows and the position tile

The wireframe lives in `design/wireframe/` (32 pages, static HTML, no build). It is the
structural source of truth: layout, content, hierarchy, states, interactions. Visual
design happens downstream against `design/BRIEF.md`.

Open `design/wireframe/index.html`.

## 1. Global shell

```
┌──────────────────────────────────────────────────────────────────────┐
│ topbar: logo · workspace · search · PAPER/LIVE ·                     │
│         sync status · alerts · kill switch · avatar                  │
├──────────────┬───────────────────────────────────────────────────────┤
│ sidebar      │ page header (title, subtitle, primary action)         │
│ Discovery    │ ───────────────────────────────────────────────────── │
│  Launches    │ content                                               │
│  Browse      │                                                       │
│  Signals     │                                                       │
│ Overview     │                                                       │
│  Dashboard   │                                                       │
│  Positions   │                                                       │
│  Performance │                                                       │
│ Trade        │                                                       │
│  Wallets     │                                                       │
│  Activity    │                                                       │
│  Reports     │                                                       │
│ Automate     │                                                       │
│  Automations │                                                       │
│  Strategies  │                                                       │
│  Backtests   │                                                       │
│ Protect      │                                                       │
│  Risk        │                                                       │
│  Alerts      │                                                       │
│ System       │                                                       │
│  Chains      │                                                       │
│  Integrations│                                                       │
│  Settings    │                                                       │
│  Security    │                                                       │
│  Developer   │                                                       │
│  Operations  │                                                       │
│  Workspace   │                                                       │
└──────────────┴───────────────────────────────────────────────────────┘
```

Persistent rules:

- Discovery leads: Browse and Signals are the first sidebar group. Reference galleries
  live under `/more`, never in the primary navigation.
- Mode badge (`PAPER` / `LIVE`) is always in the topbar; live adds a red hairline.
- Chain scope belongs to the page that owns the rows. Markets, Launches and Positions
  each carry their own chain filter in the page URL; the shell has no global chain
  control, so no chip in the topbar may imply one.
- Kill switch is always visible; it opens a confirm sheet with scope (all / chain /
  wallet / strategy).
- A global `sync` chip shows last successful chain sync per connected wallet; clicking
  opens the sync drawer.
- Empty, loading, error, offline and degraded states are designed per page, not generic.

## 2. Screens

| # | File | Page | Purpose | Key content |
|---|---|---|---|---|
| 00 | `index.html` | Gallery | Map of the wireframe + flows | page cards, flow links, legend |
| 01 | `01-sign-in.html` | Sign in | Auth entry | passkey/2FA, self-host note, recovery |
| 02 | `02-onboarding.html` | Onboarding | First run | create vault, add wallet, pick chains, risk profile, paper default |
| 03 | `03-dashboard.html` | Dashboard | Market first: what is the market doing now? | market strip (launches + trending), equity hero, PnL, exposure, open positions preview, signals, automation health, chain status |
| 04 | `04-positions.html` | Positions | What is happening now? | filters, group-by, full position tiles, closed strip, `Position protection` panel (arm form with profile picker + arms table) |
| 05 | `05-position-detail.html` | Position detail | Decide on one position | full chart, ladder, fills, ledger journal, notes, actions |
| 06 | `06-markets.html` | Markets | Find a market | screener table, chain tabs, filters, sparkline, watchlist |
| 07 | `07-token.html` | Token | Inspect and trade | chart with levels, quote panel with the pre-trade cost sheet (platform, venue, network, impact, slippage window, receiver's worst case, journal fill fee), safety, liquidity, holders, own position |
| 08 | `08-wallets.html` | Wallets | Where is my money? | multi-wallet cards, per-chain balances, custody, policy, sync |
| 09 | `09-wallet-detail.html` | Wallet detail | Operate one wallet | balances, allowances, activity, policy, keys/custody, sweep |
| 10 | `10-automations.html` | Automations | What runs without me? | list, status, last run, pause, logs, `Armed protection` list (armed arms with honest states) |
| 11 | `11-automation-builder.html` | Automation builder | Compose a rule | trigger → conditions → actions, scope, dry run, simulation |
| 12 | `12-strategies.html` | Strategies | Installed plugins | cards with mode, paper gate, allocation, params, permissions |
| 13 | `13-strategy-detail.html` | Strategy detail | Install and control | manifest, params form, grants, gate evidence, intents, logs |
| 14 | `14-signals.html` | Signals | What is the market saying? | feed, source filters, score, evidence, routing |
| 15 | `15-alerts.html` | Alerts | How do I get told? | rules, channels, quiet hours, delivery log, test |
| 16 | `16-backtests.html` | Backtests | Does it hold up? | runs table, metrics, equity curve, comparison, data snapshot |
| 17 | `17-risk.html` | Risk | How much can I lose? | exposure by chain/token/strategy, limits, drawdown, circuit breakers, kill switch |
| 18 | `18-ledger.html` | Activity / ledger | Where did the money go? | trades, transfers, fees, PnL attribution, export, reconcile |
| 19 | `19-reports.html` | Reports | What do I owe / earn? | PnL statement, tax lots, CSV/Parquet export, period selector |
| 19a | `19a-performance.html` | Performance | Where does the edge come from? | PnL attribution, R-multiples, benchmark, cost drag, exclusions |
| 20 | `20-chains.html` | Chains & RPC | Which chain is healthy? | registry, tiers, RPC latency, gas, finality, circuit state |
| 21 | `21-integrations.html` | Integrations | Connect venues | CEX keys (read-only first), DEX aggregators, data providers |
| 22 | `22-settings.html` | Settings | Make it mine | workspace, appearance, locale, units, notifications defaults, danger zone |
| 23 | `23-security.html` | Security | Who can act? | sessions, 2FA/passkeys, vault, key custody, audit log, revoke |
| 24 | `24-developer.html` | Developer | Build on it | API keys, scopes, webhooks, SDK, sandbox, event log |
| 25 | `25-operations.html` | Operations | Is the node healthy? | services, queues, breakers, incidents, backups, upgrade |
| 26 | `26-workspace.html` | Workspace | Who else is here? | members, roles, invites, approval policy |
| 27 | `27-states.html` | State gallery | Every non-happy state | loading, empty, stale, unsellable, no route, offline, quarantine |
| 28 | `28-mobile.html` | Mobile | Responsive proof | dashboard, positions, tile, kill switch |
| 29 | `29-components.html` | Components | Handoff reference | tile anatomy, chart frame, table, badges, drawers, modals, toasts |
| 30 | `30-launches.html` | Launches | What launched most recently? | births feed table, age, curve progress, dev history, safety, LP custody, Trade |

## 3. Primary flows

1. **First run:** sign in → create vault → add watch-only wallet → chains → paper mode →
   dashboard with empty states → install strategy from local path → paper.
2. **Promote a strategy:** strategy detail → paper gate evidence → live approval →
   allocation limits → first live intent (confirm) → alert.
3. **Manual trade:** markets → token → quote panel (buy + full-size sell) → risk preview
   (size, impact, fees, worst case) → confirm → position tile.
4. **Exit:** dashboard tile → sell now (one click) or ladder/trail proposal → fill →
   ledger entry.
5. **Automate:** automation builder → trigger/condition/action → dry run over 30 days →
   enable in paper → promote.
6. **Incident:** alert → ops/risk page → pause chain or kill switch → investigate event
   log → resume with a recorded reason.
7. **Recovery:** wallet unsynced → sync drawer → reconcile adjustment → review → resume.

## 4. Position tile (canonical, from tile-lab)

The tile is a 9-zone instrument. It must render from one `PositionView`; no client math.

```
┌─────────────────────────────────────────────────────────────┐
│  Z1  avatar  NAME / SYMBOL            PnL %   PnL $  equity │
│  Z2  mark dot  price · age · source   [PAPER] [venue] [age] │
│  Z3  chart: entry · stop · trail · arm/bank/rungs · peak     │
│  Z4  MCAP  LIQ  HOLDERS  1H  M5  BSR      (live | at entry) │
│  Z5  IN    BANKED   REALIZED   UNREAL                       │
│  Z6  BAG %  PRE-BANK/BANKED/RIDING/LADDERED   NEXT +n%      │
│  Z7  mark · exec · gate · rule      net · fees              │
│  Z8  hold budget bar / RIDING      [SELL NOW] [WRITE OFF]   │
│  Z9  note (one line, full on hover)                         │
└─────────────────────────────────────────────────────────────┘
```

Rules:

- `—` for unknown. Never zero, never a fabricated number.
- PnL headline uses the executable mark; USD and SOL shown together.
- Ladder levels appear on the chart only when 75% of the distance is covered (no
  look-ahead into levels the position has not earned).
- `unsellable` / `dust` / `no_sell_route` are visually distinct and keep the last
  executable quote.
- Hold budget shrinks; when `RIDING`, it is replaced by `RIDING · NO MAX HOLD`.
- Actions: `SELL NOW` (single click), `WRITE OFF` (two-step arm, paper-only), and a
  `⋯` menu (add note, export journal, open in explorer).
- Every cell's tooltip carries source + age + exact value.

**States to design:** entering, open-pre-bank, trail-armed, banked, riding, laddered,
unsellable, dust, stale mark, closed, quarantined.

## 5. Data each screen reads

| Screen | Primary API |
|---|---|
| Dashboard | `/portfolio/summary`, `/positions?state=open`, `/signals?limit=8`, `/automations?status=active`, `/chains/health` |
| Positions | `/positions?view=tile&group=…`, `/tokens/{chain}/{mint}/candles` (one closed-OHLCV series per open position; an empty series renders the empty state, never a chart) |
| Position detail | `/positions/{id}`, `/positions/{id}/fills`, `/positions/{id}/journal` |
| Markets | `/markets?chain=…&sort=…` |
| Token | `/tokens/{chain}/{mint}`, `/tokens/{chain}/{mint}/candles`, `/quote`, `/positions?token=…` (chart: closed candles with source+age; safety: the snapshot's safety object, `—` for any field the store lacks) |
| Wallets | `/wallets`, `/wallets/{id}/balances` |
| Automations | `/automations`, `/automations/{id}/runs`, `/protection/arms` (armed list) |
| Positions protection | `/protection/arms`, `/protection/profiles`, `/protection/profiles/effective?chain=…` |
| Strategies | `/strategies`, `/strategies/{id}` |
| Signals | `/signals`, `/signals/sources` |
| Backtests | `/backtests`, `/backtests/{id}` |
| Risk | `/risk/exposure`, `/risk/limits`, `/risk/breakers` |
| Ledger | `/ledger/entries`, `/ledger/export` |
| Performance | `/performance?period=…&mode=…`, `/performance/attribution` |
| Chains | `/chains`, `/chains/health` |
| Ops | `/ops/services`, `/ops/queues`, `/ops/incidents` |

## 6. Interaction and accessibility rules

- Keyboard: `g d` dashboard, `g p` positions, `g w` wallets, `g a` automations,
  `/` search, `k` kill switch sheet, `Esc` closes drawer.
- Tables: sortable, sticky header, row → detail, bulk selection with a count.
- Numbers: tabular figures, right-aligned, sign and colour together (never colour alone).
- Charts: hover crosshair, keyboard focusable points, text alternative with last value.
- Modals: focus trap, restore focus, destructive actions require typing/arming.
- Colour is never the only signal; every status has a label and an icon.
- Density: comfortable and compact; default comfortable at ≥ 1440 px.

## 7. Responsive

| Breakpoint | Behaviour |
|---|---|
| ≥ 1440 | Full 240px sidebar, 3-column tiles, 2-column detail, 52px topbar |
| 1101–1439 | 240px sidebar; 3-column tiles → 2; 4-column grids → 2 |
| 769–1100 | 240px sidebar; split panes → 1 column; 5/6-column grids → 3 |
| ≤ 768 | Sidebar hidden, bottom nav (Home, Browse, Signals, Positions, More), all grids 1 column, tables scroll, topbar wraps |

The desktop sidebar can be collapsed to a 72px labelled rail with the explicit persisted
toggle; it never auto-collapses at 1100px. The topbar is 52px above the mobile breakpoint;
at ≤1200px its low-priority chips hide and the search flexes, and at ≤768px the search takes
a full row and the topbar wraps.

## 8. Position protection delta (issue #103)

- **Positions (`04-positions.html#protection`):** a `Position protection` panel below the
  positions table. The form picks an open position, an arm kind (`stop-loss`,
  `trailing stop`, `take-profit`), a trigger (price or percent; trailing offers percent
  or price distance), and a profile (manual/smart or `—`). Smart rules render verbatim.
  The arms table below lists every arm for the account with kind, trigger, status,
  trailing peak, and Edit / Cancel actions. Edits apply immediately while armed.
- **Automations (`10-automations.html#armed-protection`):** an `Armed protection` list
  shows the same arms with counts of armed arms. States are honest: `armed`,
  `triggered`, `cancelled`, `EXPIRED`. Unknown triggers and peaks render as `—`,
  never `0`.
- **Data:** `GET /api/v1/protection/arms`, `POST/PATCH /arms`, `POST /arms/{id}/cancel`,
  `POST /arms/{id}/evaluate`, `GET/POST /profiles`, `GET /profiles/effective?chain=…`.
  All money strings are decimal strings from the server; the web formats only.
  Every value is PAPER. Live execution is not part of this change.

## 9. Quick-trade bar delta

- **Token (`07-token.html`):** a `Quick trade` bar sits above the paper ticket. It offers
  amount presets (50 / 100 / 250 / custom), a buy/sell toggle, a `Preview` control (Enter
  also works), a single submit that places the paper order through the existing
  `/api/v1/paper/orders` endpoint, and the landed pre-trade cost sheet before the click.
  Presets are browser-local USD notionals (no server state); a custom amount is
  remembered as a preset in the same browser. The bar repeats the quote's route, source
  and age. After a filled buy, an explicit `Protect this position` button opens a
  prefilled stop-loss arm form; arming is never automatic and uses the same armed
  confirmation as the positions page. Honest states (quote failure, NO_SELL_ROUTE,
  insufficient cash, paper-mode badges) match the existing ticket; unknown stays `—`.
- **Data:** `/quote`, `/paper/orders`, `/protection/arms` (no new API surface).

## 10. Handoff to visual design

`design/BRIEF.md` defines the design tokens, tone, component inventory and the pages in
priority order. The wireframe deliberately uses grayscale and dashed placeholders so that
layout, hierarchy and content are judged before aesthetics. Do not add content that is not
in the wireframe without updating this document. The dark "Instrument" skin is primary: `tokens.css` keeps the light `:root` byte-identical and adds `[data-theme="dark"]`, and the web app defaults to dark with a topbar toggle persisted as `ezchain.theme` in `localStorage`. The inline bootstrap in `app/layout.tsx` reads the stored choice and otherwise guesses once from `prefers-color-scheme` (light only when the system prefers light), so a stored light skin survives reloads.
