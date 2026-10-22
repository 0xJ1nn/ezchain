# Contributing to ezCHAIN

Read [`project.md`](project.md) (canonical rules), [`AGENTS.md`](AGENTS.md) (quick
reference) and [`docs/CONTRACT.md`](docs/CONTRACT.md) (invariants) before your first PR.

## Ways to contribute

- **Chain adapter** — a new chain or a tier promotion; start with
  `.github/ISSUE_TEMPLATE/chain_adapter.yml`.
- **Venue adapter** — execution for a DEX/aggregator.
- **Strategy plugin** — in your own repository; register it with the strategy template.
  Strategy code is not accepted here.
- **Platform** — UI, API, engine, ledger, risk, infra. One scoped PR.
- **Docs / wireframe** — content and structure; visual work follows `design/BRIEF.md`.

## Setup

Prerequisites: Node.js 22+, pnpm 9 (`npm install -g pnpm@9`), Python 3.11, Postgres 16,
and Chrome for the Playwright smoke test.

```bash
git clone https://github.com/0xJ1nn/ezchain.git
cd ezchain
node tools/sync-agent-stubs.mjs
pnpm install
PYTHON=python3.11 bash tools/setup-python.sh
createdb ezchain
cp .env.example .env
.venv/bin/alembic -c apps/api/alembic.ini upgrade head
pnpm dev                       # web UI at http://localhost:3000
bash tools/ci.sh
```

The API, engine, web UI and Postgres schema are built; the executor and live execution are
not. Docker Compose remains a blueprint and is not verified. Migrations are forward-only:
add a revision with `alembic -c apps/api/alembic.ini revision --autogenerate`, review it,
and never put destructive operations in `upgrade()`. Mutations need
`EZCHAIN_API_TOKEN`; the web server injects it, so never send the token to the browser.
Playwright uses the installed Chrome channel; install Chrome if the UI test cannot find
it.

## PRs

1. Branch from fresh `origin/main` (`feat/…`, `fix/…`, `docs/…`, `design/…`).
2. Conventional Commits, `git commit -s`.
3. Fill the PR template, including the exact verification commands you ran.
4. Keep the diff scoped. Unrelated cleanups belong in their own PR.
5. Update docs in the same PR. A stale doc is a bug.

Money-path PRs (ledger, risk, executor, venue adapters) need two approvals.

## Licensing

Inbound = outbound. App code and adapters: AGPL-3.0-or-later. `packages/sdk-py`, docs and
design: Apache-2.0. By submitting a contribution you grant the project a perpetual,
worldwide, non-exclusive, royalty-free license to use, reproduce, modify, distribute and
relicense your contribution under the project's current or future license. This is a
license grant, not a copyright assignment.

Do not paste code from a project whose license is incompatible. If you are unsure, ask in
the issue first.

## Hard rules (instant close)

- Bundled trading strategies, signals sold as returns, or performance claims without a
  harness.
- Committing keys, seed phrases, API tokens, `.env` files, or customer data.
- Removing an invariant or a safety check to make a test pass.
- Copy-pasting code without attribution/license compatibility.
