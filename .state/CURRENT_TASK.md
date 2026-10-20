# CURRENT TASK

**Objective:** deliver the ezCHAIN project scaffold: learnings from solbot v1/v2/v3 and
pumpbot, architecture and infra design, and a 31-page low-fidelity wireframe for the
visual design pass.

**Scope in:** docs, wireframe, infra blueprint, PR workflow, gates.
**Scope out:** runtime code, strategy implementations, chain adapters.

**Acceptance:** wireframe opens, every page in the manifest, all gates green, PR merged.

**Verification:** `node tools/check-wireframe.mjs`, `node tools/check-links.mjs`,
`node tools/check-secrets.mjs`, `node tools/sync-agent-stubs.mjs --check`.

**State:** delivered 2026-10-20 — 31-page wireframe, docs, infra, gates green. Next: M1 runtime scaffold (ADR-0001 boundaries first).
