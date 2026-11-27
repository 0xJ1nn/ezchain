# Contributing to ezCHAIN

Read the invariant contract ([docs/CONTRACT.md](docs/CONTRACT.md)) and the contributor guide
([AGENTS.md](AGENTS.md)) before your first PR.

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
bash tools/setup-web.sh
PYTHON=python3.11 bash tools/setup-python.sh
createdb ezchain
cp .env.example .env
.venv/bin/alembic -c apps/api/alembic.ini upgrade head
pnpm dev                       # web UI at http://localhost:3000
bash tools/ci.sh
```

The API, engine, web UI and Postgres schema are built; the executor and live execution are
not. A runnable PAPER Compose stack and its recorded verification are described in the
README. Migrations are forward-only: add a revision with
`alembic -c apps/api/alembic.ini revision --autogenerate`, review it, and never put
destructive operations in `upgrade()`. Mutations need `EZCHAIN_API_TOKEN`; the web server
injects it, so never send the token to the browser. Playwright uses the installed Chrome
channel; install Chrome if the UI test cannot find it.

## Scope, tests and claims

- One topic per pull request. Define what is in and out of scope before starting; unrelated
  cleanups belong in their own PR. Do not refactor code the task did not ask you to touch.
- New behaviour ships with a failing-first test. A reproduced bug gets a regression test;
  money math and ledger rules get property tests. Explain why a test changed.
- Report only what you ran. Quote the command and its observed result; if you did not run a
  check, write "not verified". Paper results are labelled `PAPER`.

## Local gate

Run `bash tools/ci.sh` before opening a PR. The gate checks links, secrets, wireframe
integrity and the runtime suites. Quote its result in the PR description. A red gate is not
merged, and no step may be skipped.

## PRs

1. Branch from fresh `origin/main` (`feat/…`, `fix/…`, `docs/…`, `design/…`).
2. Conventional Commits, `git commit -s`, one topic per PR.
3. Fill the PR template, including the exact verification commands you ran.
4. Keep the diff scoped. Unrelated cleanups belong in their own PR.
5. Update docs in the same PR. A stale doc is a bug.

Money-path PRs (ledger, risk, executor, venue adapters) need two approvals.

## Licensing

App code and adapters: AGPL-3.0-or-later, with the additional terms in [`NOTICE`](NOTICE).
`packages/sdk-py`, docs and design: Apache-2.0.

Every contribution is made under the [Contributor License Agreement](CLA.md). You keep your
copyright; the agreement lets the maintainer distribute your work under the open-source licence
and also under the commercial licence described in `NOTICE`. It also promises that accepted work
always stays available under an open-source licence. Agree by putting this line in each pull
request description:

> I have read and agree to the ezCHAIN Contributor License Agreement, version 1.0.

The name and logo are covered by [`TRADEMARKS.md`](TRADEMARKS.md), not by the code licences. A fork
that you distribute or host needs its own name.

Do not paste code from a project whose license is incompatible. If you are unsure, ask in
the issue first.

## Hard rules (instant close)

- Bundled trading strategies, signals sold as returns, or performance claims without a
  harness.
- Committing keys, seed phrases, API tokens, `.env` files, or customer data.
- Removing an invariant or a safety check to make a test pass.
- Copy-pasting code without attribution/license compatibility.
