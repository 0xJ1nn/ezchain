## What & why

<!-- The problem, not the diff. Link the issue. -->

Closes #

## Scope

- In:
- Out:

## Risk

- [ ] Touches a money path (ledger / risk / executor / venue adapter)
- [ ] Touches key custody or auth
- [ ] Adds or changes a migration
- [ ] Changes an invariant in `docs/CONTRACT.md` (ADR required)
- [ ] Changes public API / SDK (versioning note required)
- [ ] Visual/design change (wireframe + brief updated)

## Verification

<!-- Exact commands and observed results. "Not run" is allowed with a reason. -->

```
node tools/check-wireframe.mjs
node tools/check-links.mjs
bash tools/ci.sh
```

## Screens

<!-- For design PRs: before/after, and which wireframe page changed. -->

## Checklist

- [ ] One topic, scoped diff, no unrelated refactors
- [ ] Docs updated in this PR (architecture/contract/wireframe as applicable)
- [ ] `CHANGELOG.md` updated for user-visible changes
- [ ] No secrets, keys, dumps, `_tmp_*` or generated files committed
- [ ] Conventional Commit + `git commit -s` (DCO)
- [ ] I did not edit a test to green without a stated reason
- [ ] Claims in this PR are measured, not inferred (label PAPER/BACKTEST where relevant)
