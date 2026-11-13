# Clean-clone gate and publication audit

Measured 2026-11-13 on branch `chore/clean-clone-gate` at commit
`f649ddb01b67dca40336853261f3a6c2e14428d7`, based on `c763cb3`. Every result below is the
output of the command shown with it; nothing is inferred.

## Environment

macOS (Darwin), Node v26.13.0, pnpm 9.15.9, Python 3.11.18, Playwright 1.64.1 using the
installed Chrome channel. The host exports `NODE_ENV=production`; the setup scripts and
the gate normalise it rather than trusting the caller.

## Clean clone

The branch is not pushed, so it was cloned from the checkout with no shared objects into
an empty directory:

```bash
git clone --branch chore/clean-clone-gate file://<branch checkout> <temporary directory>
cd <temporary directory>
git rev-parse HEAD
# f649ddb01b67dca40336853261f3a6c2e14428d7
ls -d node_modules .venv
# no output: neither exists in a fresh clone
bash tools/setup-web.sh
# Done in 2.1s using pnpm v9.15.9 — 537 packages
# web: ok — eslint, tsc and Playwright installed
PYTHON=python3.11 bash tools/setup-python.sh
# Successfully installed ezchain-adapters ezchain-api ezchain-core ezchain-engine ezchain-mcp ezchain-sdk
```

The clone holds 613 tracked files and three commits above the base.

## Gate

The web suite is forced because the branch changes no web path:

```bash
EZCHAIN_GATE_WEB=always bash tools/ci.sh; echo "exit=$?"
```

```
agent stubs: ok
tokens: ok
wireframe: ok (32 pages, 32 files)
links: ok
secrets: ok
131 files already formatted
Success: no issues found in 118 source files
1446 passed, 5 skipped, 1 warning in 29.76s
✓ Compiled successfully in 4.0s
197 passed (4.0m)
ci: ok
exit=0
```

The default invocation is green as well:

```bash
bash tools/ci.sh; echo "exit=$?"
# 1446 passed, 5 skipped, 1 warning in 28.93s
# web gates skipped: the diff touches no web paths (EZCHAIN_GATE_WEB=always forces them)
# ci: ok
# exit=0
```

Playwright runs the fixture-mode suite; `apps/web/playwright.config.ts` excludes the
live-paper spec, so the live API path is not covered here. The browser tests used the
installed Chrome channel, not bundled Chromium. `tools/check-migrations.sh` did not run:
it requires `EZCHAIN_TEST_DATABASE_URL`.

### Failure path

A clone installed with `NODE_ENV=production` in the environment receives no
devDependencies from pnpm. Before the fix, the gate stopped at the first web step:

```
apps/web lint$ eslint .
apps/web lint: sh: eslint: command not found
ERR_PNPM_RECURSIVE_RUN_FIRST_FAIL @ezchain/web@0.0.0 lint: `eslint .`
```

After the fix, the same clone stops with the cause and the remedy:

```
web dependencies are incomplete (eslint missing): run bash tools/setup-web.sh
```

Both runs exit 1. `tools/setup-web.sh` installs from `pnpm-lock.yaml` after clearing
`NODE_ENV`; `tools/ci.sh` clears `NODE_ENV` and `PYTHONPATH` before any step runs.

## Publication audit

The checkout has no finding at HEAD. The CLI also reads every line added on every
reachable ref, so its total includes superseded history that cannot be removed without
rewriting published commits, which `docs/WORKFLOW.md` forbids.

The range added by the branch:

```bash
python3 <publication tool>/check_range.py <branch checkout> origin/main chore/clean-clone-gate --profile public
# exit=0, no findings
```

HEAD, scanned with the function the CLI uses:

```
findings=0 blocking=0
```

The full CLI:

```bash
python3 <publication tool>/publication_audit.py --repo <branch checkout>
```

```
clean-clone  -  blocked  block=14  {'draft-voice': 1, 'internal-id': 57, 'internal-marker': 8, 'local-path': 3, 'promotion-claim': 2}
```

All 71 findings are history-only. The 14 blocking entries are added lines from earlier
commits, or a file deleted at HEAD but still present in published history:

| Kind | Location | Content |
|---|---|---|
| draft-voice | `docs/CHAINS.md:23` | a progress-narration phrase on the unmerged remote branch `origin/fix/markets-browse` |
| internal-marker | `docs/DECISIONS.md:607` | a review-role word beside an accepted decision |
| internal-marker | `.state/HANDOFF.md:69-75` | seven provider and model lines |
| local-path | `.state/HANDOFF.md:4` | a repository path under a home directory |
| local-path | `docs/evidence/agent-integration/openclaw.md:12` | an install path under a home directory |
| local-path | `docs/evidence/agent-integration/openclaw.md:20` | an extension path under a home directory |
| promotion-claim | `docs/DECISIONS.md:903` | the commitment sentence reworded at HEAD |
| promotion-claim | `docs/PRODUCTION-READINESS.md:188` | the readiness sentence reworded at HEAD |

The 57 review-level entries are phase identifiers in superseded revisions:

| File | Findings |
|---|---|
| `ROADMAP.md` | 17 |
| `docs/PLAN-LIVE-PAPER.md` | 16 |
| `docs/TODO.md` | 10 |
| `.state/PR_LOG.md` | 5 |
| `.state/CURRENT_TASK.md` | 4 |
| `apps/api/src/ezchain_api/routers/strategies.py` | 2 |
| `apps/api/src/ezchain_api/routers/paper.py` | 1 |
| `apps/api/src/ezchain_api/services/paper.py` | 1 |
| `tests/executor/test_pipeline.py` | 1 |

## What changed

- `.githooks/pre-push` is executable in the index, so a checkout that installs the hooks
  actually runs it.
- `tools/setup-web.sh` installs the JavaScript workspace from the lockfile with a
  normalised environment; `CONTRIBUTING.md`, `README.md`, `docs/VISUAL-TESTING.md` and
  `docs/TODO.md` use it in their setup instructions.
- `tools/ci.sh` clears `NODE_ENV` and `PYTHONPATH`, and names the setup script when the
  web dependencies are incomplete.
- The roadmap, plan, todo, two decisions and the OpenClaw evidence now use public prose:
  no phase identifiers, no home paths, no readiness claim.

## Remaining

Closing the 71 history-only findings requires rewriting published commits: deleting
`.state/HANDOFF.md` from history and removing superseded lines. That is a maintainer
action; this branch does not rewrite history. The unmerged remote branch
`origin/fix/markets-browse` also needs deletion or rewriting for the `docs/CHAINS.md`
line to disappear from a clone that fetches every branch.
