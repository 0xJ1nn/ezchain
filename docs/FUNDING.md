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
- **Hosting through partners.** People who do not want to run their own server can rent one
  from a hosting partner that offers a ready-made ezCHAIN image. The server belongs to the
  user's account with that provider, under the provider's own terms. The ezCHAIN project does
  not operate it, has no access to it, and never holds, sees or recovers the keys or funds on
  it. Partners pay the project for the right to use the ezCHAIN name (see
  [TRADEMARKS.md](../TRADEMARKS.md)) or share revenue; they do not buy any control over a
  user's server.
- **Optional paid services.** An ezCHAIN account for conveniences that need a server the
  project runs: prepaid credits for AI models, market data and chain RPC, official signed
  builds with automatic updates, and a relay that lets a phone reach a user's own ezCHAIN
  without opening ports. Every service is optional. Bringing your own model, data and RPC keys
  always works, and the app works fully without an account.

Paid services change convenience, never the safety of the platform, and none of them handles a
user's trading keys or funds.

## 3. What ezCHAIN does not do for money

ezCHAIN takes no fee on trades and never earns from a user's fills. The project holds no user
keys or funds and runs no custodial wallet; it never transfers or safe-keeps a user's crypto.
The software trades with keys the user keeps in its encrypted vault on their own device or their
own server, by their own choice and under their own control. The project sells no signals,
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
hosting partners, the paid services and the commercial licence are not available today — they
are named as the ways the project intends to be funded, and this page will say when each one
exists.
