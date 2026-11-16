# FUNDING — what is free and how ezCHAIN is funded

The platform is software the user runs on their own keys. Money protects that arrangement only
if it never buys a safety feature, so the first rule is what cannot be sold.

## 1. Free forever

The following stay free in every version of ezCHAIN, including a hosted paper deployment:

- the platform itself — the app, API, agent server, adapters and strategy SDK; the app and
  adapters are AGPL-3.0-or-later and the SDK and docs are Apache-2.0 ([LICENSE](../LICENSE),
  [NOTICE](../NOTICE));
- the risk rails — position sizing, caps, freshness and stale-mark refusal, and the
  fail-closed rules that stop an order with unknown inputs;
- the kill switch and the approval gate, including an agent's own approval queue;
- paper mode, the backtester and the append-only ledger, with replay and reconciliation;
- self-hosting with the official installer.

Risk rails, the kill switch, paper mode and the ledger are not a paid tier in another form. A
team feature may add convenience around them; it may not remove, weaken or meter them.

## 2. How the project is funded

These are the intended revenue streams, in the order the project expects to use them:

- **Sponsorship and ecosystem grants.** Community sponsorship and grants from projects whose
  infrastructure ezCHAIN supports. This needs no product change. The repository's sponsor
  button is not configured yet, so there is no sponsorship account to name here.
- **Support and setup contracts.** Priority response, deployment help and custom venue
  adapters for firms and serious individuals who self-host. Every fix still ships publicly
  under the same licence.
- **Commercial licence.** A licence for firms that will not publish their changes under the
  AGPL. It is a software licence, priced per company per year, and is possible because of the
  AGPL and the contributor licence agreement ([CLA.md](../CLA.md)).
- **Hosting.** A hosted paper workspace for people who do not want to run Docker, Postgres
  and upgrades. It holds no keys and runs no live execution; live trading stays on the user's
  own server with the user's own keys, as described in [SELF-HOSTING.md](SELF-HOSTING.md).
  Provider referral programmes come before certified hosting partners.

Paid extras on hosted paper — history, compute, agent credits or shared workspaces — change
the convenience of a paper workspace, never the safety of the platform.

## 3. What ezCHAIN does not do for money

ezCHAIN takes no fee on trades and never earns from a user's fills. It holds no keys, no funds
and no custodial wallet; it never transfers or safe-keeps a user's crypto. It sells no signals,
buy or sell calls, recommendations or "strategy that returns X%" claims, and it does not tell a
user what to trade. Exchange referral and affiliate income are avoided because they would
compromise venue neutrality. No advertising claims returns, profits or safe trading — the
project reports what it measured, and says when nothing was measured.

These limits are a design constraint, not a marketing position. A stream that needs ezCHAIN to
hold a key, take a cut of a fill or recommend a trade is a stream the project declines.

## 4. Scope of this document

This is the funding policy for the documentation and repository. It records intent and the
boundaries, not a promise of income. Nothing here is legal or tax advice; the person or entity
taking money is responsible for the rules that apply to them. The sponsorship account, the
hosted paper service and the commercial licence are not shipping today — they are named as
the ways the project intends to be funded, and this page will say when each one exists.
