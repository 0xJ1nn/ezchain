# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). SemVer.

## [Unreleased]

### Added

- `apps/web` M1 scaffold: Next.js shell, dashboard, positions page with four tile states,
  markets screener and token page with a fail-closed quote panel, all fixture-driven, with
  responsive bottom nav.
- Generated design tokens (`design/tokens.json` → `design/tokens.css` and
  `packages/ui/src/tokens.css`) and the `tools/build-tokens.mjs` gate.
- Playwright smoke/screenshot checks for dashboard, positions and markets at 1440 and
  390 px, plus token page desktop, mobile and fail-closed screenshots.
- Markets render evidence: wireframe-vs-implementation and mobile screenshots under
  `docs/evidence/m1-ui/`; token page evidence adds desktop, mobile and fail-closed shots.
- Project scaffold: product, architecture, domain model, chain adapter contract,
  strategy SDK, security, infra and PR workflow docs.
- 31-page low-fidelity wireframe in `design/wireframe/` with a manifest and gates.
- Performance screen: attribution, R-multiple distribution, benchmark and cost drag.
- Deployment blueprint: compose services, Caddy edge, Prometheus alerts, OTel collector.
- CI gates: agent stubs, design tokens, wireframe integrity, markdown links, secret scan,
  and the pnpm lint/typecheck/build/test runtime gate.

### Changed

- `docs/CHAINS.md` now presents a **target** support matrix with a `Verified today`
  column; no chain is claimed as implemented.
- `docs/PRODUCT.md` labels the v1 scope table as a target, not shipped.
- `README.md` and `apps/web/README.md` status now match the merged M1 surfaces.

### Fixed

- Position-tile charts no longer widen the page at 390 px (#23).
- Dashboard bank activity and the position-tile peak now agree with the token fixture;
  a cross-page test guards the constants (#38).

### Removed

- Nothing yet.

## [0.0.0] — 2026-10-20

Initial design drop. No runtime code.
