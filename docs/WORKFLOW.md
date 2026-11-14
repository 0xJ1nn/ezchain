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

Review is by CODEOWNERS. A pull request whose diff touches `packages/core-py/`,
`apps/api/alembic/`, `services/engine/`, `services/executor/` or `packages/adapters/` is
money-path and needs **two distinct approvers**; every other pull request needs **one**.
`tools/check_reviews.py` is the decision, and the merge gate runs it:

```bash
git diff --name-only origin/main...HEAD | python3 tools/check_reviews.py <pr>
python3 tools/check_reviews.py <pr> --diff changed.txt --json
```

What counts as evidence — this is the whole rule, and there is no other door:

- **An approver is one GitHub login.** Either an `APPROVED` review, or a comment by an
  `OWNER`, `MEMBER` or `COLLABORATOR` with a line that begins with `approved`
  (case-insensitive; `**approved**` and "looks approved to me" do not count).
- **One login is one approver.** A review and a comment by the same login, or two
  comments by it, are still one. A bot login never counts.
- **A thread that cannot be read is a refusal.** A `gh` failure, malformed JSON or a
  document without reviews and comments exits `1`, and the gate treats any non-zero exit
  as no review. Nothing degrades to "probably fine".
- `CHANGES_REQUESTED` and `DISMISSED` reviews count for nothing.

The check is honest about what independence cannot mean here: **`0xJ1nn` is the only
account that authors and reviews on this repository**, and the work is written by agents
under it. GitHub allows one `APPROVED` review per login per pull request, so one account
can supply exactly one approver however many times it comments. Two approvers therefore
require a second account with write access; until one exists, a money-path pull request
is refused by the gate rather than waved through. If a second agent login is added, it is
a distinct login and counts — the check does not know or care whether both are machines,
and no document here should pretend a single-account approval is two reviews.

To leave the refusal on record, run the checker by hand and quote its output in the pull
request; `--json` prints the diff's classification, the required count, each approver
with its source, and the verdict.

## 4. Gates

**GitHub Actions is not available on this account** (billing lock, 2026-10-20). The gate
is local and mandatory: `bash tools/ci.sh` before every main push. Install the tracked
hook once per clone (`bash tools/install-hooks.sh`); it runs the gate for `main` only,
matching the sibling repos' policy. When Actions is restored, the same commands become the
workflow — do not add a workflow that cannot run.

`tools/ci.sh` is the single entrypoint:

| # | Gate | Command |
|---|---|---|
| 1 | Generated agent stubs fresh | `node tools/sync-agent-stubs.mjs --check` |
| 2 | Generated tokens fresh | `node tools/build-tokens.mjs --check` |
| 3 | Wireframe integrity | `node tools/check-wireframe.mjs` |
| 4 | Docs links | `node tools/check-links.mjs` |
| 5 | Security | `node tools/check-secrets.mjs` |
| 6 | Dependency pins | `tools/check_pins.py --fast` — exact versions, sha256 for every Python pin, integrity for every Node resolution, and every declared range still satisfied |
| 7 | Runtime gates | `ruff`/`mypy`/`pytest` when the Python artifacts exist; `pnpm -r lint`, `pnpm -r typecheck`, `pnpm --filter @ezchain/web build`, `pnpm -r test` |

All seven steps run today. Python, invariant and adapter-contract suites activate as those
artifacts land; compose validation is not in the gate yet. Every gate that is committed
must be runnable on this machine.

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
4. Render the implementation and compare it with the design; source comparison is not
   evidence. See [`docs/VISUAL-TESTING.md`](VISUAL-TESTING.md).
5. Screenshots/verification are committed only for release evidence, not per PR.

## 7. Releases

- SemVer. `vX.Y.Z` tag from `main` only.
- Release notes: what changed, upgrade notes, known limitations (never omit), image
  digests, restore-drill result.
- Pre-release: `-rc.N`, at least 48 h of paper soak.
- Rollback: redeploy the previous image; migrations are forward-only, so a rollback that
  needs a schema revert is an incident, not a routine action.

## 8. Issue labels

`bug`, `feature`, `design`, `documentation`, `chain`, `venue`, `strategy-sdk`, `security`,
`money-path`, `good first issue`, `needs-triage`, `blocked`. `money-path` is intended to
require a second review; the branch ruleset does not enforce that yet, so the merge gate
does it from the diff (§3), and a label is not what protects a path.

## 9. Working agreements

- The code is truth; docs are updated in the same PR.
- No scratch files in the repo. `_tmp_*`, dumps, screenshots and research scripts are
  rejected by CI.
- Every incident gets a failure-ledger entry (symptom, root cause, law) within a week.
- Do not reopen settled decisions without an ADR; link the old one.
