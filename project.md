# ezCHAIN Engineering System

> **Canonical development rules.** This document defines how ezCHAIN is developed and
> maintained. `AGENTS.md` is the short operational entry point; every AI profile,
> contributor guide and tool stub points here. If a document conflicts with this one,
> this document wins — unless the conflict is with an explicit maintainer instruction or
> a normative invariant in [`docs/CONTRACT.md`](docs/CONTRACT.md).

This system is adapted from the sibling project ezCORE. The rules are deliberately the
same: one task, one branch, one PR, tests before claims, human authority, and no silent
architecture changes. Only the domain examples differ.

---

## Current project state

- Version: `0.0.0`
- Release status: design / pre-runtime
- Main branch: protected by process, not by ceremony
- CI: GitHub Actions is not available on this account (billing lock, 2026-10-20). The
  gate is local and mandatory: `bash tools/ci.sh`.
- Active milestone: **M1 — runtime scaffold**
- Current focus: web UI against `design/wireframe/`; API/engine/executor/ingest next
- Architecture status: specified in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md);
  deviations require an ADR in [`docs/DECISIONS.md`](docs/DECISIONS.md)
- Runtime code: web UI scaffold only. API, engine, executor and ingest are not built.
  Do not describe them as shipped.

---

## 1. Maintainer interaction rule

The maintainer is actively learning software engineering and open-source maintenance.
Agents must not merely execute; they must teach when a decision is important.

For every important technical decision:

1. Explain the problem in simple language.
2. Explain what the current system does.
3. Explain the proposed change.
4. Explain why it is being proposed.
5. Explain what could break.
6. Explain alternatives.
7. Explain how we will test it.
8. Ask for or accept maintainer direction when the decision is significant.

Never hide an architectural decision inside code. The objective is not only to improve
ezCHAIN; it is to make the maintainer progressively better at understanding, operating
and maintaining it.

---

## 2. Primary objective

Maximum progress **while maintaining maximum stability**.

The project must evolve quickly without sacrificing existing functionality, architecture,
user trust, maintainability, or the maintainer's ability to understand what is happening.

Never optimize for raw coding speed at the expense of stability. Never optimize for
architectural purity at the expense of useful progress.

The correct target is:

**fast + systematic + tested + isolated + understandable development.**

"Zero breakage" is the target, but no process can guarantee zero bugs. The system must
minimize regression risk and detect regressions as early as possible.

---

## 3. Priority of authority

1. Explicit human instruction (the maintainer says so).
2. Project architecture and safety rules, including [`docs/CONTRACT.md`](docs/CONTRACT.md)
   invariants.
3. This document.
4. Task-specific instructions.
5. Agent preference.

An AI profile may never override an explicit human instruction. If two instructions
conflict, stop and ask. Do not invent a third interpretation.

---

## 4. Single source of truth

Do not create conflicting copies of these rules.

- `project.md` is canonical.
- `AGENTS.md` is the operational quick reference.
- `CLAUDE.md`, `.github/copilot-instructions.md`, `.codex/instructions.md`,
  `.opencode/instructions.md` and `.cursor/rules/ezchain.mdc` are generated pointers.
- `CONTRIBUTING.md` explains contribution mechanics; it must not contradict this file.
- `docs/WORKFLOW.md` explains git/PR/release mechanics in more detail.

If instruction files conflict, the priority order in §3 applies. Fix the conflict in the
same PR that exposes it.

---

## 5. Mandatory first step — reconnaissance

Before modifying code for any non-trivial task, stop and inspect:

- `project.md` (this file)
- [`docs/CONTRACT.md`](docs/CONTRACT.md) — normative invariants
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — deployables, boundaries, data flow
- [`README.md`](README.md) — what the project is
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — relevant ADRs
- [`.state/CURRENT_TASK.md`](.state/CURRENT_TASK.md) — current task and handoff
- `git status`, current branch, relevant source and tests

Also inspect the actual repository rather than trusting documentation blindly. The code
is the current implementation truth; docs can go stale. If they disagree, fix the doc in
the same PR.

Determine:

- what the system currently does
- where the requested behavior lives
- which interfaces are involved
- what depends on the affected system
- what tests already exist
- what could regress

Never start editing because the request "sounds simple".

---

## 6. One task at a time

Every meaningful task has a clearly defined scope.

Good: "Render the position tile from the shared `PositionView` fixture."

Bad: "Improve positions." Bad: "Make the UI better." Bad: "Refactor the platform."

Large objectives must be decomposed. Use:

**one problem → one plan → one branch → one implementation → one test set → one review
→ one PR → merge.**

Do not combine unrelated work.

---

## 7. Scope lock

Once a task is defined, establish:

- **In scope:**
- **Out of scope:**
- **Files expected to change:**
- **Files that must not change:**
- **Tests required:**

If implementation reveals another problem: record it, explain it, open an issue, and
continue with the original task. Do not expand scope without explicit approval.

This rule exists because AI agents naturally discover many "improvements" while working.
Do not let discovered improvements become accidental rewrites.

---

## 8. No unrelated refactoring

Never do this:

> Task: "Fix the position tile spacing."
> Agent: rewrites the shell, upgrades dependencies, reorganizes routes, renames
> components, changes the design system, and updates the ledger schema.

Feature work and cleanup work are separate. Bug fixes remain bug fixes. Refactoring is
its own task and PR whenever practical.

---

## 9. Investigation before implementation

For non-trivial tasks, the first agent pass is read-only. Return:

```
PROBLEM:
ROOT CAUSE:
AFFECTED SYSTEMS:
PROPOSED SOLUTION:
FILES TO MODIFY:
FILES NOT TO MODIFY:
TEST PLAN:
RISKS:
ALTERNATIVES:
```

Do not modify code during this phase.

---

## 10. Plan before implementation

Before implementation, produce a concise plan understandable to the maintainer:

- what will change
- why
- how
- what will remain unchanged
- how existing behavior will be protected
- how the change will be tested
- what risks remain

If the plan requires a major architectural decision, stop and ask. Do not silently decide.

---

## 11. Smallest safe change

Prefer the smallest implementation that correctly solves the problem.

Avoid unnecessary abstractions, speculative infrastructure, premature optimization and
large rewrites. Prefer:

**small change + clear test + known effect**

over:

**large rewrite + many moving parts + uncertain effect.**

---

## 12. Existing functionality is sacred

Existing working functionality is presumed valuable. Do not remove or alter working
behavior merely because another approach looks cleaner.

Before changing an existing interface, determine who uses it, whether it is public,
whether adapters/strategies/tests depend on it, and whether external contributors could
depend on it. If a breaking change is necessary, explain it first.

---

## 13. Platform boundaries are not suggestions

ezCHAIN is a modular monolith with two hard boundaries:

```
apps/web  →  apps/api  →  services/engine  →  services/executor  →  chains/venues
                    ↘        services/ingest
```

- `apps/web` owns presentation, routing and formatting. It never computes PnL, risk or
  ledger math, and never sees key material.
- `apps/api` owns HTTP/WS, auth, validation and read models. It never executes orders or
  performs accounting math.
- `services/engine` owns strategy runtime and automation scheduling. It never signs and
  never writes ledger entries.
- `services/executor` is the only key holder and the only service allowed to write fill
  journal entries.
- `services/ingest` acquires and normalizes chain/market data. It never makes trading
  decisions.
- `packages/core-py` owns domain types, money, ledger and risk policy. It performs no I/O
  and imports no framework code.
- `packages/sdk-py` is the public plugin contract. It imports only core types.
- `packages/adapters/*` own one chain or venue each. Core code branches on capabilities,
  never on chain names.

Enforced boundaries are listed in `docs/ARCHITECTURE.md` §3. A change that crosses one
requires an ADR and maintainer approval.

---

## 14. The invariants are the product

[`docs/CONTRACT.md`](docs/CONTRACT.md) is normative. A change that violates an invariant
is rejected regardless of how good it looks. The short list:

1. The ledger is the only accountant.
2. Paper, testnet and live are one pipeline.
3. Fail closed on money.
4. Keys live only in the executor.
5. No strategy code in this repository.
6. Chains are capabilities, not branches.
7. Every number has one home.
8. Migrations are forward-only and never destructive.
9. Replay or it didn't happen.
10. One position model, one tile.
11. Boring process, hard limits.
12. Claim only what was measured.

Before proposing a change to a money path or a boundary, state which invariant it touches.
If it touches one, it needs an ADR and explicit maintainer approval.

---

## 15. Tests are a safety system

Every bug that can reasonably be reproduced receives a regression test.

A regression test must satisfy:

- broken implementation → test fails
- fixed implementation → test passes

Tests must verify meaningful outcomes, not merely execute code. Cover normal behavior,
edge cases, invalid input, failure paths, lifecycle, cleanup and compatibility where
relevant.

Money math, ledger invariants and risk policy need property tests, not only examples.
Adapters must pass the shared adapter contract suite. Strategies must pass the SDK
conformance suite.

---

## 16. Test before and after

Before implementation, run the relevant existing tests where practical. After
implementation, run:

1. formatter
2. linter / static analysis
3. type checks
4. relevant unit tests
5. relevant integration tests
6. full suite where practical
7. build of affected targets
8. platform/browser checks where relevant

If a test fails, do not simply edit it until it passes. Determine whether the
implementation is wrong, the test is wrong, the behavior was misunderstood, the
environment is broken, or a dependency changed. Explain the cause.

Never edit a test to green without a reason in the PR.

---

## 17. Adversarial review

After implementation, perform a hostile self-review. Assume the implementation may be
wrong. Try to break it.

Check null/empty inputs, invalid input, unexpected state, initialization failure,
shutdown, repeated initialization, resource cleanup, concurrency, error handling, API
compatibility, performance regressions, unrelated systems and test quality.

Ask: **"How could this break a user or lose money?"** Then investigate those cases.

---

## 18. Independent review

For important changes, use a second agent or model as an independent reviewer. The
reviewer must not simply summarize the implementation. Instruct it:

> Assume this implementation contains a bug. Try to find it.

The reviewer inspects the diff, tests, architecture, scope and regression risk. It does
not modify code unless explicitly assigned. This creates:

**implementer → independent reviewer → human maintainer.**

Two AIs agreeing is not evidence. Evidence comes from files, tests, measurements and
runtime behavior.

---

## 19. Git rules

- `main` is the known-good branch.
- Never casually develop directly on `main`.
- Never force-push `main`.
- Never rewrite shared history.
- Never make large experimental changes directly on `main`.
- Use branches: `feat/…`, `fix/…`, `docs/…`, `design/…`, `refactor/…`, `test/…`,
  `chore/…`, `release/…`. Unfinished work goes on a pushed `wip/…` branch.
- One topic per branch, one owner per branch.

Parallel agents use worktrees, not the same working tree:
`git worktree add ../ezchain-<topic> -b <type>/<topic> origin/main`.

---

## 20. Commits

Commits represent logical changes. Conventional Commits, signed off:

```
feat(web): render the position tile from PositionView
fix(ledger): commit position and journal in one transaction

Refs: #123
Signed-off-by: Name <email>
```

Avoid "stuff", "fix", "changes", "final", "final2". Types: `feat`, `fix`, `docs`,
`design`, `refactor`, `test`, `chore`, `perf`, `build`, `ci`, `revert`. Scope = package
or area (`web`, `api`, `engine`, `executor`, `adapters`, `core`, `sdk`, `infra`, `docs`).

---

## 21. Pull requests

Every meaningful change uses a PR, even for a solo maintainer. The PR is a safety
checkpoint, not ceremony.

The PR body follows `.github/PULL_REQUEST_TEMPLATE.md`:

- what & why (the problem, not the diff)
- scope (in/out) and linked issue
- risk (money path, keys, migrations, API, design)
- verification commands and observed results
- screenshots for UI/design changes
- checklist

Never merge merely because it compiles or because an AI says it is fixed.

---

## 22. PR merge policy

Merge only when:

- scope is correct
- implementation is understandable
- tests pass
- build passes
- diff is clean
- no unexpected files changed
- regression risk is understood
- documentation is updated where necessary
- the maintainer approves

Squash-merge focused branches. The squash subject is the conventional commit; the body
keeps the PR summary and verification. Delete the branch after merge. Never merge a red
PR "to unblock".

Money-path PRs (ledger, risk, executor, venue adapters) need two reviewers, or one
reviewer plus the maintainer's explicit approval in the thread.

---

## 23. Main branch protection

Configure GitHub protection when repository infrastructure is ready:

- pull request required
- status checks required
- force pushes disabled
- branch deletion restricted as appropriate

Do not create bureaucracy for a solo maintainer. The goal is protection, not ceremony.

---

## 24. CI / the local gate

GitHub Actions is not available on this account. The gate is local and mandatory:

```bash
bash tools/ci.sh
```

It runs, as the artifacts exist: agent-stub freshness, wireframe integrity, markdown
links, secret scan, and runtime gates when runtime code is present (format, lint, types,
unit, invariants, adapter contract, OpenAPI diff, compose validation).

A red gate is never merged. Every PR quotes the command and its observed exit status. If
a gate was not run, say so and why. Never add a workflow that cannot run.

---

## 25. Dependency policy

Do not add a dependency casually. Before adding one, determine:

- why it is needed
- whether existing functionality solves the problem
- maintenance status and license
- security considerations
- platform/browser compatibility
- binary or build impact
- long-term maintenance cost

Explain the decision to the maintainer. Avoid dependency bloat. Lockfiles are owned by
the PR that intentionally changes them; never rewrite them as a side effect.

---

## 26. Security

Never commit API keys, passwords, private keys, seed phrases, tokens, credentials,
personal secrets or machine-specific sensitive paths. Check diffs before committing.

If a secret is exposed: stop, report it, rotate it. Deleting it from the latest file is
not a fix; assume the history is compromised.

The secret scan in `tools/ci.sh` is a floor, not a guarantee.

---

## 27. License and distribution boundaries

ezCHAIN is AGPL-3.0-or-later; `packages/sdk-py`, docs and design are Apache-2.0;
trademarks are separate. See `LICENSE`, `TRADEMARKS.md` and `CONTRIBUTING.md`.

Before incorporating external code, assets, libraries, algorithms or documentation:

- identify the source
- identify the license
- check compatibility
- record provenance
- follow attribution and redistribution requirements
- explain implications to the maintainer

Never copy code because it is technically useful. Never remove copyright notices. If
licensing is unclear, stop and ask.

---

## 28. Third-party code

Never silently copy code from another trading bot, exchange SDK or repository. Before
integrating external code:

1. identify the source
2. identify the license
3. check compatibility
4. record provenance
5. follow attribution requirements
6. explain implications to the maintainer

Keep independently developed ezCHAIN code clearly separated from third-party code.

---

## 29. Technical debt

AI will discover unrelated problems. Do not fix them automatically. Record them in a
GitHub issue (or `docs/TECHNICAL-DEBT.md` when one exists) and continue.

Technical debt discovered during a task stays separate from the current implementation
unless it blocks the task.

---

## 30. Roadmap

Maintain `ROADMAP.md` when the project has one. Separate **current**, **next**, **later**
and **experimental**. The roadmap is not permission for uncontrolled development.
Roadmap items become focused issues before implementation.

---

## 31. Changelog

Maintain `CHANGELOG.md` under `[Unreleased]`. Record meaningful features, fixes,
compatibility changes, architectural changes and user-facing changes. Do not clutter it
with every tiny internal edit.

---

## 32. Versioning

Semantic Versioning: `MAJOR.MINOR.PATCH`.

Small increments by default: `0.0.1 → 0.0.2`, not a jump to `0.1.0`. A band change is
the maintainer's explicit call. Published versions are immutable: never renumber, reuse
or skip a tag.

Every version bump updates all surfaces together: version in the relevant manifest, the
`Current project state` line above, a dated `CHANGELOG.md` section, and release notes.

---

## 33. Documentation

Maintain `README.md`, `AGENTS.md`, `project.md`, `docs/ARCHITECTURE.md`,
`docs/CONTRACT.md`, `docs/DECISIONS.md`, `CHANGELOG.md` and `CONTRIBUTING.md`.

- README: what ezCHAIN is and why it matters.
- ARCHITECTURE: how the software works.
- CONTRACT: what must never break.
- DECISIONS: why the architecture is what it is.
- AGENTS/project: how humans and AI must behave.
- CHANGELOG: what changed.

Do not rewrite documentation unnecessarily during unrelated tasks. A stale doc is a bug;
fix it in the PR that makes it stale.

---

## 34. README quality

The GitHub repository is part of the product. Keep it intentional and maintained:

- clear branding
- clean structure
- screenshots when runtime screens exist
- what the project is and is not
- install/run instructions that actually work
- documentation links
- contribution instructions
- license and trademark boundaries

Do not turn the README into an enormous wall of text. Do not claim runtime features that
do not exist.

---

## 35. UI/UX and branding

The visual identity is part of the product. Do not casually modify finalized branding,
tokens, typography, spacing or the design system.

Before modifying finalized UI:

- understand the existing design system
- identify the exact problem
- preserve the established visual language
- make isolated changes
- compare before/after renders
- avoid "AI redesigning everything"

Do not change unrelated UI while implementing backend functionality.

The wireframe (`design/wireframe/`) is the structural source of truth. Visual styling is
downstream (`design/BRIEF.md`). A deliberate deviation is recorded in
`design/OWNER-APPROVED-DELTAS.md` with a reason. Screenshot comparison is required for
visual PRs; comparing source code alone is not evidence.

---

## 36. Performance

Do not optimize from intuition. Before significant optimization:

1. identify the actual bottleneck
2. measure where practical
3. establish a baseline
4. implement an isolated change
5. measure again
6. verify correctness

Never sacrifice correctness for an assumed performance improvement.

---

## 37. Platform compatibility

ezCHAIN targets a browser UI and a Linux-first self-hosted runtime. Do not assume
behavior on one browser, OS or chain applies to all. Platform-specific code stays
isolated. When changing a shared abstraction, consider every supported surface.

---

## 38. Error handling

Errors must be explicit, understandable, recoverable where possible, logged
appropriately and non-destructive. Do not silently swallow errors. Do not use crash
behavior for recoverable user errors unless there is a strong technical reason.

Money paths fail closed. A missing quote, stale oracle, unknown token or unreachable risk
service means no new order — never a fabricated zero.

---

## 39. User data safety

Existing user data is valuable: wallets, ledger entries, settings, notes, strategy
parameters, backtest artifacts and API keys.

Before changing a data format, consider backward compatibility, migration, corruption,
rollback, partial writes and old versions. Never casually change persistent formats.
Never assume users start with a clean installation.

---

## 40. Database, storage and migrations

If persistent structures change:

1. identify the existing format
2. identify users on old versions
3. design a forward-only migration
4. test old → new
5. test new → load
6. test corrupted/partial data
7. never assume a clean install

No `wipe`, `clean_slate`, `full_reset` or `try: ALTER TABLE except: pass`. A data reset is
an operator action, documented and audited. Migrations are forward-only (CONTRACT I8).

---

## 41. Generated code and generated files

Do not hand-edit generated files unless the project explicitly requires it. Find the
source or configuration that generates them, change the source, regenerate, and validate.

Agent stubs and generated schemas are rebuilt, never hand-edited. `node
tools/sync-agent-stubs.mjs --check` must be green.

---

## 42. No blind automation

Do not execute destructive commands without understanding their consequences. Be
especially careful with `rm`, `git reset`, `git clean`, force push, mass replacement,
dependency upgrades, migration scripts and generated-code replacement.

If a command can destroy work, stop and explain before executing it.

---

## 43. Git status before and after

Before meaningful work: `git status`.

After meaningful work: `git status`, `git diff`, `git diff --stat`. Ensure only expected
files changed. Investigate unexpected changes before committing.

---

## 44. No hidden changes

Never silently rewrite files unrelated to the task, modify configuration, change
dependencies, modify lockfiles unnecessarily, change build settings, reformat the whole
project, alter environment configuration, or commit generated files by hand.

If something changes unexpectedly, stop and report it.

---

## 45. Model failure policy

AI models can hallucinate, misunderstand architecture and produce plausible but incorrect
code. Never treat model confidence as evidence.

Evidence comes from source code, tests, compiler output, static analysis, runtime
behavior, benchmarks, reproducible results and authoritative technical sources.

---

## 46. When AI is uncertain

Do not guess. Say: "I found two plausible interpretations," then explain them.

If the difference materially affects architecture, money, security or user-visible
behavior, ask the maintainer.

---

## 47. External research

When technical information may be outdated or important, research authoritative sources:
official documentation, specifications, upstream repositories, standards and primary
sources. Do not confidently invent APIs, chain behavior, exchange semantics or platform
behavior.

---

## 48. Open-source maintainer mode

Treat every public change as something another developer may inspect. Code should be
readable, explainable, testable and maintainable. Avoid clever code when straightforward
code is available.

---

## 49. Contributor-friendly architecture

Future contributors must be able to understand where a feature belongs, where an adapter
belongs, how tests work, how to build, how to submit changes and which rules apply.

Good architecture is for future humans, not only for AI.

---

## 50. AI agent roles

Use specialized roles rather than asking one agent to do everything:

1. **Explorer** — read-only; finds files, architecture, dependencies, tests.
2. **Architect / planner** — read-only; root cause, solution, scope, risks, test plan.
3. **Implementer** — implements the approved task under scope lock.
4. **Tester** — tries to break it; read-only unless assigned to write tests.
5. **Reviewer** — independently reviews diff, architecture, tests, regression risk.
6. **Documentation agent** — updates docs only when required.

Do not let a documentation agent modify unrelated code.

---

## 51. Multi-agent safety

Do not allow multiple agents to modify the same branch or worktree simultaneously unless
the workflow explicitly supports it. Preferred: **one active implementer per
branch/task**. Other agents review or investigate independently.

Parallel implementation uses separate git worktrees and separate branches. Shared files
(shell, tokens, lockfiles, generated schemas) have one owner per PR; coordinate in the PR
body, not in commits.

---

## 52. Model specialization and diversity

Do not assume the most expensive model must do everything. Use appropriate models:

- cheap/free models: exploration, docs, boilerplate, simple tests, repository searches,
  repetitive page work
- stronger models: architecture, complex debugging, money paths, adversarial review,
  migrations

For important changes, consider using different models for implementation and review.
Independent models may catch different mistakes. Agreement between two AIs is still not
evidence; tests and runtime behavior are.

---

## 53. Release candidates

Before a release:

1. main is clean
2. the gate passes
3. recent changes are reviewed
4. CHANGELOG is current
5. version is correct
6. builds pass
7. important surfaces are verified
8. no secrets
9. documentation is current
10. tag and release notes are created from `main`

Do not release from an unreviewed feature branch.

---

## 54. Regression-first thinking

For every change ask: **"What existing user behavior could this break?"** — not only
"does the new feature work?" New functionality is useless if it destroys existing
functionality.

---

## 55. Stability budget

When choosing between a large change with a large regression surface and smaller
incremental changes with easier validation, prefer the smaller changes unless there is a
compelling reason. Large changes must justify their risk.

---

## 56. Emergency bugs

If a critical user-facing bug appears: create `fix/<specific-problem>`, investigate,
reproduce, add a regression test, fix, run targeted and broader tests, review, merge, and
release a patch when appropriate. Do not panic and rewrite unrelated systems.

---

## 57. When a change breaks something

Do not hide it, disable the test, blame the user, randomly patch multiple systems or
continue adding features.

Instead: reproduce, identify the regression, determine what changed, revert if necessary,
add a regression test, fix systematically, validate, and document if important.

Every failure should improve the project's defenses.

---

## 58. Failure must be constructive

Failures are information. Explain what failed, why, what was learned, what should change,
what should be tested and how to prevent recurrence.

Do not catastrophize. Fix the smallest underlying problem.

---

## 59. Don't let AI death-spiral

If an agent repeatedly changes code without converging: stop. Return to a known-good
state, reproduce, understand, plan, make a small change, test.

Never allow endless AI patch chains. Inspect the diff, revert the problematic change and
restart from known-good.

---

## 60. Known-good checkpoints

Maintain stable checkpoints. A release tag is a recoverable known-good state. Before
risky work, ensure a clean commit or checkpoint exists.

---

## 61. Experimental work

Experimental ideas are isolated on `experiment/<name>` or another clearly isolated
branch. Never let experimental code silently become production architecture. If
successful, convert it into a proper implementation plan.

---

## 62. Product ideas vs engineering tasks

A product idea is not automatically an implementation task. For "add X", first determine
requirements, users, data model, security, failure handling, costs, dependencies and
compatibility. Then create implementation tasks.

---

## 63. Architectural changes

Architecture changes require extra caution. Before implementation explain:

```
CURRENT ARCHITECTURE
PROBLEM
PROPOSED ARCHITECTURE
ALTERNATIVES
BENEFITS
COSTS
RISKS
MIGRATION PLAN
ROLLBACK PLAN
TEST PLAN
```

The maintainer must understand and approve major architecture changes. Record the
decision as an ADR in `docs/DECISIONS.md`.

---

## 64. Do not optimize for AI

The codebase must not be shaped merely to make AI coding easier. Optimize for correctness,
humans, maintainability, modularity, users and contributors. AI is a tool; the project
must remain understandable without it.

---

## 65. Maintainer dashboard

Keep track of current version, branch, active task, open bugs, open features, technical
debt, gate status, known regressions and next release. The maintainer should always be
able to answer: **"What state is ezCHAIN currently in?"**

`.state/CURRENT_TASK.md` and `.state/PR_LOG.md` are the lightweight dashboard.

---

## 66. Task priority

When choosing the next task, generally consider:

1. critical regressions
2. user-facing crashes or data loss
3. build/gate failures
4. security/licensing problems
5. architectural blockers
6. important usability problems
7. high-value features
8. performance improvements backed by evidence
9. documentation
10. cosmetic/internal cleanup

Do not blindly follow this list when context requires another priority. Explain significant
priority changes.

---

## 67. Never let momentum destroy quality

If the project receives sudden attention, do not respond by rushing features, rewriting
everything, accepting every idea, adding dependencies randomly, abandoning tests, merging
directly to main or producing giant AI-generated commits.

Increased attention makes engineering discipline more important.

---

## 68. Project health over short-term hype

A repository can gain users faster than its architecture matures. Do not confuse
popularity with stability. Prioritize reliability, documentation, reproducible builds,
clear issues, clean releases, contributor onboarding and regression protection.

---

## 69. Change impact classification

Before implementation, classify:

- **Low risk:** docs, isolated tests, tiny fix, non-functional cleanup.
- **Medium risk:** shared UI logic, public API changes, dependency changes, adapter
  interface changes.
- **High risk:** architecture, ledger, risk, executor, key custody, migrations,
  cross-chain abstraction, large dependency migration.

Higher-risk changes require deeper investigation, stronger testing and explicit maintainer
understanding.

---

## 70. Final diff review

Before every meaningful PR, inspect the complete diff. Ask:

- did we change only what we intended?
- did formatting touch unrelated files?
- did dependencies change?
- did generated files change?
- did public APIs change?
- did tests change appropriately?
- did documentation need updating?
- is there debugging code?
- are there secrets?
- is there dead code?
- is there accidental behavior change?

If anything is unexplained, investigate before merge.

---

## 71. Final implementation report

Every completed task reports:

```
SUMMARY
ROOT CAUSE
IMPLEMENTATION
FILES CHANGED
FILES NOT CHANGED
TESTS RUN
BUILD STATUS
REGRESSION CHECK
RISKS
KNOWN LIMITATIONS
TECHNICAL DEBT DISCOVERED
FOLLOW-UP ISSUES
```

Do not claim something was tested if it was not tested. Do not claim something works if it
was not verified.

---

## 72. Absolute honesty

Never fabricate test results, build results, benchmarks, compatibility, source provenance,
documentation, GitHub state, files inspected or commands executed.

If something could not be verified, say: **"Not verified."**

---

## 73. Human understanding check

Before a major decision, ensure the maintainer understands:

- **What** are we changing?
- **Why** are we changing it?
- **Risk:** what could break?
- **Alternatives:** what else could we do?
- **Validation:** how will we know it worked?
- **Rollback:** how can we undo it?

Do not bury important decisions inside implementation details.

---

## 74. The golden development loop

Every meaningful task:

1. understand the request
2. inspect the repository
3. read the project rules
4. identify scope
5. identify risks
6. investigate
7. explain important decisions
8. plan
9. create a branch
10. implement the smallest safe change
11. add/update tests
12. run formatter
13. run linter/static analysis
14. run relevant tests
15. run broader tests
16. build affected targets
17. inspect the diff
18. perform adversarial review
19. independent review when appropriate
20. update documentation when required
21. open a PR
22. human maintainer reviews
23. merge
24. delete the branch
25. update changelog/version when appropriate
26. release when appropriate
27. return to the next isolated task

---

## 75. The golden rule

Never ask: **"How much code can we make today?"**

Ask: **"How much reliable progress can we make today without damaging what already
works?"**

---

## 76. Final principle

ezCHAIN should grow like a professional software project — systematic engineering, not
chaotic vibe coding.

The AI can move extremely fast. The process keeps that speed safe.

The maintainer provides vision, product direction, final decisions and priorities. AI
provides investigation, implementation, testing, review, documentation and automation.
Git provides history, isolation and rollback. CI provides verification. Tests provide
regression protection. Documentation provides project memory. PRs provide controlled
integration.

---

## 77. Scratch agent tooling never enters the repository

Scratch agent tooling is tooling an AI creates while doing a task: screenshot drivers,
input injectors, capture helpers, probe scripts and one-off diagnostic utilities.

These are **not** project code. They live in `/tmp` (or an equivalent ephemeral
location), are wiped when the task ends, and never become part of a commit.

Only real, permanent tests belong in `test/`. Before finishing any task, confirm
`git status --short` shows no scratch files.

Screenshot evidence for a design PR is the exception: a reviewed image may be committed
to `docs/evidence/` or `design/visual/` when the PR body references it.

---

## 78. Push hygiene

Every push is verified file-by-file. Before pushing, confirm:

- `git status --short`
- `git diff --stat`
- the exact commit list

Never push AI-generated documentation clutter — session notes, status files, summary
files, plan files — unless the maintainer explicitly approves it as important for the
open-source repository.

README, CHANGELOG and developer documentation are legitimate exceptions, and are still
reviewed. Every push report lists the exact files pushed. Before finishing any task,
`git status --short` must be clean.

---

*This is the permanent ezCHAIN development system, adapted from the ezCORE engineering
system. It applies to all future ezCHAIN development.*
