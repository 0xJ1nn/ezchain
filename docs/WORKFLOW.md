# WORKFLOW — git, PRs, gates, releases

Adapted from the sibling repos' conventions, keeping what worked and dropping what didn't.

## 1. Branches

- `main` is always releasable. Never commit to it directly.
- Branch from fresh `origin/main`; never stack unless the PR states the dependency.
- Prefixes: `feat/`, `fix/`, `docs/`, `design/`, `refactor/`, `test/`, `chore/`,
  `release/`. Unfinished work: `wip/<topic>` pushed, never dirty.
- One topic per branch, one owner per branch. Parallel agents use worktrees
  (`git worktree add ../ezchain-<topic> -b <type>/<topic> origin/main`).
- Shared files (lockfiles, generated schemas) have one owner per PR; coordinate in the PR
  body, not in commits.

## 2. Commits

Conventional Commits, signed off:

```
feat(executor): require full-size sell quote before entry
fix(ledger): commit position and journal in one transaction

Refs: #123
Signed-off-by: Name <email>
```

Types: `feat`, `fix`, `docs`, `design`, `refactor`, `test`, `chore`, `perf`, `build`,
`ci`, `revert`. Scope = package name (`web`, `api`, `engine`, `executor`, `adapters`,
`core`, `sdk`, `infra`, `docs`).

## 3. PR contract

Every PR (even solo) uses `.github/PULL_REQUEST_TEMPLATE.md`:

1. **What & why** — the problem, not the diff.
2. **Scope** — in/out; linked issue; ADR if architecture changed.
3. **Risk** — money paths touched? keys? migrations? chain/venue behavior?
4. **Verification** — exact commands + observed results. "Not run" is allowed with a reason.
5. **Screens** — wireframe/UI diff for design PRs.
6. **Checklist** — invariant impact, docs, changelog, no secrets, DCO.

Review is by CODEOWNERS. For money-path PRs (ledger, risk, executor, adapters/venue),
two reviewers or one reviewer + the maintainer's explicit approval in the thread.

## 4. Gates

**GitHub Actions is not available on this account** (billing lock, 2026-10-20). The gate
is local and mandatory: `bash tools/ci.sh` before every main push. Install the tracked
hook once per clone (`bash tools/install-hooks.sh`); it runs the gate for `main` only,
matching the sibling repos' policy. When Actions is restored, the same commands become the
workflow — do not add a workflow that cannot run.

`tools/ci.sh` is the single entrypoint:

| # | Gate | Command |
|---|---|---|
| 1 | Generated artifacts fresh | `node tools/sync-agent-stubs.mjs --check` |
| 2 | Wireframe integrity | `node tools/check-wireframe.mjs` |
| 3 | Docs links | `node tools/check-links.mjs` |
| 4 | Format/lint | `ruff format --check`, `ruff check`, `eslint`, `prettier --check` |
| 5 | Types | `mypy --strict packages services apps/api`, `tsc --noEmit` |
| 6 | Unit + property | `pytest`, `vitest` |
| 7 | Invariants | ledger/risk/adapter contract suites |
| 8 | OpenAPI diff | generated client vs committed schema |
| 9 | Security | `gitleaks`, dependency audit, no-key-material scan |
| 10 | Compose | `docker compose config -q` (when Docker is available) |

Until runtime code exists, gates 1–3 and 9 (docs/wireframe subset) run. Every gate that is
committed must be runnable on this machine.

## 5. Merge

- Squash-merge. Subject = conventional commit; body = PR summary + verification.
- Rebase before merge if `main` moved; never merge a red PR "to unblock".
- Delete the branch after merge.
- Update `CHANGELOG.md` under `[Unreleased]` in the same PR for user-visible changes.
- Close the linked issue; remove any lane/ownership rows added for the work.

## 6. Design workflow

1. Structural change → edit `design/wireframe/` + `docs/WIREFRAME.md` + manifest.
2. Visual pass (Claude Design) → `design/BRIEF.md` as input; output is read-only source
   in `design/visual/`.
3. Implementation consumes tokens/components; deviations go in
   `design/OWNER-APPROVED-DELTAS.md` with a reason.
4. Screenshots/verification are committed only for release evidence, not per PR.

## 7. Releases

- SemVer. `vX.Y.Z` tag from `main` only.
- Release notes: what changed, upgrade notes, known limitations (never omit), image
  digests, restore-drill result.
- Pre-release: `-rc.N`, at least 48 h of paper soak.
- Rollback: redeploy the previous image; migrations are forward-only, so a rollback that
  needs a schema revert is an incident, not a routine action.

## 8. Issue labels

`bug`, `feature`, `design`, `docs`, `chain`, `venue`, `strategy-sdk`, `security`,
`money-path`, `good-first-issue`, `needs-triage`, `blocked`. `money-path` automatically
adds the second-review requirement.

## 9. Working agreements

- The code is truth; docs are updated in the same PR.
- No scratch files in the repo. `_tmp_*`, dumps, screenshots and research scripts are
  rejected by CI.
- Every incident gets a failure-ledger entry (symptom, root cause, law) within a week.
- Do not reopen settled decisions without an ADR; link the old one.
