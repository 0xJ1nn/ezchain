# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). SemVer.

## [Unreleased]

### Added

- `apps/web` M1 scaffold: Next.js shell, dashboard, positions page with the four tile
  states, typed fixtures, responsive bottom nav.
- Generated design tokens (`design/tokens.json` → `design/tokens.css` and
  `packages/ui/src/tokens.css`) and the `tools/build-tokens.mjs` gate.
- Playwright smoke/screenshot checks for the dashboard at 1440 and 390 px.
- Project scaffold: product, architecture, domain model, chain adapter contract,
  strategy SDK, security, infra and PR workflow docs.
- 31-page low-fidelity wireframe in `design/wireframe/` with a manifest and gates.
- Performance screen: attribution, R-multiple distribution, benchmark and cost drag.
- Deployment blueprint: compose services, Caddy edge, Prometheus alerts, OTel collector.
- CI gates: agent stubs, wireframe integrity, markdown links, secret scan, compose config.

### Changed

- Nothing yet.

### Removed

- Nothing yet.

## [0.0.0] — 2026-10-20

Initial design drop. No runtime code.
