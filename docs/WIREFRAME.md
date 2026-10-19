# WIREFRAME — screen inventory, flows and the position tile

The wireframe lives in `design/wireframe/` (31 pages, static HTML, no build). It is the
structural source of truth: layout, content, hierarchy, states, interactions. Visual
design happens downstream against `design/BRIEF.md`.

Open `design/wireframe/index.html`.

## 1. Global shell

```
┌──────────────────────────────────────────────────────────────────────┐
│ topbar: logo · workspace · chain filter · search · PAPER/LIVE ·      │
│         sync status · alerts · kill switch · avatar                  │
├──────────────┬───────────────────────────────────────────────────────┤
│ sidebar      │ page header (title, subtitle, primary action)         │
│ Overview     │ ───────────────────────────────────────────────────── │
│  Dashboard   │ content                                               │
│  Positions   │                                                       │
│  Markets     │                                                       │
│ Trade        │                                                       │
│  Wallets     │                                                       │
│  Activity    │                                                       │
│ Automate     │                                                       │
│  Automations │                                                       │
│  Strategies  │                                                       │
│  Signals     │                                                       │
│  Backtests   │                                                       │
│ Protect      │                                                       │
│  Risk        │                                                       │
│  Alerts      │                                                       │
│ System       │                                                       │
│  Chains      │                                                       │
│  Integrations│                                                       │
│  Settings    │                                                       │
│  Operations  │                                                       │
└──────────────┴───────────────────────────────────────────────────────┘
```

Persistent rules:

- Mode badge (`PAPER` / `LIVE`) is always in the topbar; live adds a red hairline.
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
| 03 | `03-dashboard.html` | Dashboard | Am I safe and up? | equity hero, PnL, exposure, open positions preview, signals, automation health, chain status |
| 04 | `04-positions.html` | Positions | What is happening now? | filters, group-by, full position tiles, closed strip |
| 05 | `05-position-detail.html` | Position detail | Decide on one position | full chart, ladder, fills, ledger journal, notes, actions |
| 06 | `06-markets.html` | Markets | Find a market | screener table, chain tabs, filters, sparkline, watchlist |
| 07 | `07-token.html` | Token | Inspect and trade | chart with levels, quote panel, safety, liquidity, holders, own position |
| 08 | `08-wallets.html` | Wallets | Where is my money? | multi-wallet cards, per-chain balances, custody, policy, sync |
| 09 | `09-wallet-detail.html` | Wallet detail | Operate one wallet | balances, allowances, activity, policy, keys/custody, sweep |
| 10 | `10-automations.html` | Automations | What runs without me? | list, status, last run, pause, logs |
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
| Positions | `/positions?view=tile&group=…` |
| Position detail | `/positions/{id}`, `/positions/{id}/fills`, `/positions/{id}/journal` |
| Markets | `/markets?chain=…&sort=…` |
| Token | `/tokens/{chain}/{mint}`, `/quote`, `/positions?token=…` |
| Wallets | `/wallets`, `/wallets/{id}/balances` |
| Automations | `/automations`, `/automations/{id}/runs` |
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
| ≥ 1440 | 3-column tile grid, persistent sidebar, 2-column detail |
| 1024–1439 | 2-column tiles, collapsible sidebar |
| 768–1023 | 1-column tiles, sidebar → rail, tables → cards |
| < 768 | bottom nav (Home, Positions, Markets, Automate, More), sticky mode + kill switch, tile becomes a stacked summary with a chart accordion |

## 8. Handoff to visual design

`design/BRIEF.md` defines the design tokens, tone, component inventory and the pages in
priority order. The wireframe deliberately uses grayscale and dashed placeholders so that
layout, hierarchy and content are judged before aesthetics. Do not add content that is not
in the wireframe without updating this document.
