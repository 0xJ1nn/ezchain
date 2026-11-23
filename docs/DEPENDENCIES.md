# DEPENDENCIES — the pinned set, its audit, and how a pin moves

The production set is pinned. Nothing installs from a range: `requirements*.lock` and
`pnpm-lock.yaml` are the install sources, and both the API image and a developer's
`tools/setup-python.sh` install the lock, not the range file.

| Ecosystem | Declared (human) | Pinned (installs) | Hash requirement |
|---|---|---|---|
| Python, dev + test | `requirements.txt` | `requirements.lock` | every pin carries `sha256` of every sdist and wheel |
| Python, runtime image | `requirements-runtime.txt` | `requirements-runtime.lock` | same |
| Python, API `keyring` extra | `apps/api/pyproject.toml` | `requirements-keyring.lock` | same |
| Node | `package.json` files | `pnpm-lock.yaml` | every resolution carries an `integrity` hash |

The range file is not dead weight: it is the reviewable statement of intent ("we need
`fastapi` in the 0.16x line, below 1.0") while the lock is the machine output. A reviewer
reads the range diff; a machine reads the lock.

Two more things are pinned and are checked by the same gate: the base image of each
Dockerfile, by manifest-list digest with the tag recorded above it, and every third-party
dependency a package pyproject declares, which must appear in a lock. Anything that cannot
be pinned at all is named with a reason in `tools/pins/unpinned.txt` rather than guessed.

## 0. Base images

`apps/api/Dockerfile` and `apps/web/Dockerfile` name their base image as
`repo:tag@sha256:<digest>`. The digest is the multi-arch index, so the same line resolves to
the right child on amd64 and arm64; the tag is kept because it is what a human reads. A
comment above each `FROM` records the pair the digest came from:

```
# python:3.11-slim -> sha256:07d48240…
FROM python:3.11-slim@sha256:07d48240… AS builder
```

To move one:

```bash
docker buildx imagetools inspect python:3.11-slim   # or: crane digest python:3.11-slim
```

Take the top-level `Digest`, change the `FROM` line and its comment together in one commit,
and rebuild. `tools/check_pins.py --fast` fails on a `FROM` that names an image without a
digest, and on a digest with no tag comment, so the two cannot drift apart. A
`FROM <stage>` reference is not a base image and is not asked to carry one.

The two `FROM` lines of the API image and the two of the web image must stay on the same
digest; the checker does not compare them, so a `docker buildx imagetools inspect` on each
is the human step.

## 1. What is proven, and what is not

Proven on this machine (2026-11-14, macOS arm64, CPython 3.11.18, Node 26.13.0,
pnpm 9.15.9):

- `tools/check_pins.py --fast` — every pin is an exact `==` version, every Python pin
  carries hashes, every Node resolution carries integrity, every declared range is still
  satisfied by its pin, every package pyproject's third-party requirement is pinned or
  named as a gap, and every Dockerfile base image is a digest with its tag comment. This is
  the static part of step 5 of `tools/ci.sh` and needs no network; the advisory audit in the
  same step does.
- `tools/check_pins.py --full` — a fresh venv installs `requirements.lock` with
  `--require-hashes --no-cache-dir` and `pip freeze` matches the pins exactly: 43 pinned,
  43 installed.
- `tools/check_pins.py --full --runtime` — same for `requirements-runtime.lock`:
  31 pinned, 31 installed.
- `tools/check_pins.py --full --offline` with `EZCHAIN_WHEELHOUSE` set — the same
  install from a local wheelhouse with `--no-index`: 43 from a 43-wheel directory, and
  31 from a 31-wheel directory for the runtime set. The wheelhouse digests are in
  [docs/evidence/dependencies/pins.md](evidence/dependencies/pins.md).
- `tools/audit_pins.py --python` against the live advisory service last exited 0 on
  **2026-11-14**; it was not re-run here because `pip-audit` is absent. The Node audit was
  re-run on **2026-11-23** and now exits 0 after the four advisories below were fixed at
  the pin. What a clean exit means, exactly: no advisory was published or matched against
  those pins as of that date. Nothing here speaks for a later advisory.
- The base-image digests were resolved from the registry on 2026-11-14 and fetched back by
  digest; no image was pulled or built.
- `bash tools/ci.sh` and the full suite (2099 passed, 20 skipped).

Not proven here, and not claimed:

- **Another platform.** The full installs ran on macOS arm64. The locks carry hashes for
  every published wheel of each version, so a Linux install resolves from the same pins,
  but no Docker daemon exists on this host and the image build was not run. The Linux
  install is unverified until someone runs it.
- **A cold clone.** The offline path started from a wheelhouse this host produced in the
  same session, not from a cache someone else filled. A wheelhouse built on another
  machine, or with a stale lock, is a different claim and was not run. What the pins do
  give is an exact hash per artifact, so a stale wheelhouse fails loudly instead of
  installing a different build.
- **`services/ingest[firehose]`.** Its pin is a git tag
  (`pumpfun-firehose @ git+https://…@v0.5.0`), which has no hashes to verify. It is an
  optional extra, absent from every lock, and installing it requires a network and the
  tag to exist. It is the only declared dependency that cannot be pinned, so it is named
  with its reason in `tools/pins/unpinned.txt`; no version was invented for it.
- **`keyring`.** `requirements-keyring.lock` is generated and audited, but no deployment
  installs it, so it is unexercised outside the resolver.
- **Two clean builds with identical digests.** This is the second half of the release
  acceptance and it has not been run on this host — the machine has too little free memory
  for two simultaneous clean installs. §5 gives the drill; until it is run and recorded,
  byte-for-byte reproducibility is a procedure, not a measured result. This item does not
  close that acceptance: a release machine must record both digests first.

## 2. The audit step

```bash
python3 tools/audit_pins.py --python    # the three Python locks, via pip-audit (OSV/PyPI)
python3 tools/audit_pins.py --node      # the pnpm workspace, via the npm registry
python3 tools/audit_pins.py --report saved-pip-audit.json   # judge a report offline (needs saved-pip-audit.json.provenance)
bash tools/pins/install.sh --audit      # the Python path through the install wrapper
```

The audit is wired into step 5 of `tools/ci.sh` beside the static pin check. On a machine
with `pip-audit` and `pnpm` it runs both commands, so one missing tool cannot hide the
other's verdict. An air-gapped gate replays a saved report through the declared
`EZCHAIN_GATE_AUDIT_REPORT` channel; the ambient `EZCHAIN_AUDIT_REPORT` is scrubbed at the
top of the gate, so a caller's shell cannot select that path. A replay is accepted only
with a `.provenance` sidecar binding the report's digest and every lock's digest to this
checkout, and the replay is logged. A report that is unbound, stale or unreadable fails the
gate rather than being skipped. A missing audit tool is an explicit "not a pass" skip on a
local machine; a run declared as CI (`CI=true`) or release (`EZCHAIN_GATE_RELEASE=1`) fails
closed without both live verdicts.

It **fails** — exit 1 — on any advisory whose id is not allowlisted, naming the pin, the
id and the fixed version. Exit 2 means the audit could not run (tool absent, no network,
unreadable report); the script says so and the gate treats it as a failure, never a pass.

`tools/pins/audit-allowlist.txt` is the only way an advisory stops failing the build. Each
line needs an id and a reason; an entry without a reason is itself an error, so the file
cannot quietly become a mute button. It is empty today.

`pip-audit` is not a pinned dependency: it is an audit tool, and pinning it would add a
resolver-shaped dependency to the thing being audited. Install it where the audit runs:

```bash
pip install 'pip-audit>=2,<3'
```

Offline, the verdict is the same code path: run `pip-audit --format json -o report.json`
where a network exists, copy the report and its lock digests, write `report.json.provenance`
with `report_sha256`, `locks`, `generated_at` and `source`, then judge it with `--report`.
The sidecar makes a replay a declared, checkable act rather than an ambient file.

**Audit status 2026-11-23: Node clean, Python not re-run.** The Python locks could not be
audited here — `pip-audit` is not installed — so the last clean Python verdict is the one
recorded on 2026-11-14 in [the evidence doc §5](evidence/dependencies/pins.md); nothing here
claims a newer Python result. The Node audit was re-run on 2026-11-23 and now exits 0 after
four moderate advisories were fixed at the pin rather than allowlisted: `minimatch`
(GHSA-g3hv-75wg-m249) moved to 11.0.1 for the vulnerable 9.x and 10.x resolutions, and
`next` (GHSA-7qx7-33qx-rqx2, GHSA-f3mh-rxg3-f5g2, GHSA-pc58-hwjq-p385) moved to 15.5.29.
The overrides and the regenerated lock are in `package.json` and `pnpm-lock.yaml`; the
dated verdict is in [the evidence doc §10](evidence/dependencies/pins.md). An entry in
`tools/pins/audit-allowlist.txt` needs a written reason and is a decision, not a fix.

What the audit does **not** cover, stated plainly so nobody over-reads the green run:

- **Nothing after 2026-11-14.** A CVE published tomorrow against a pin that is clean today
  is invisible to a gate that is not run. This is why §3 has a cadence, not only a command.
- **The base-image operating systems.** `python:3.11-slim` and `node:22-alpine` carry
  Debian and Alpine packages that no Python or npm advisory database sees. An image scan
  (Trivy, `docker scout`) is the tool for that, no daemon exists on this host, and it is
  not built. The digests pin *which* image; they say nothing about what is inside it.
- **`pumpfun-firehose`.** It is not in any lock, so no lock-based audit reaches it. See
  `tools/pins/unpinned.txt`.
- **The local workspace packages.** `ezchain-*` are this repository's code, reviewed as
  code, not as an advisory subject.

## 3. How a pin moves

1. **Change the declaration, not the lock.** Edit `requirements.txt`,
   `requirements-runtime.txt`, or the `keyring` extra in `apps/api/pyproject.toml`; or
   `package.json` for Node. The lock is generated, never hand-edited.
2. **Regenerate.**

   ```bash
   PYTHON=python3.11 bash tools/pins/compile.sh    # all three Python locks
   bash tools/setup-web.sh                         # or: pnpm install --lockfile-only
   ```

   `compile.sh` pins its own resolver (`pip-tools==7.6.2`) and uses the 3.11 interpreter,
   because a lock is resolved for one interpreter.
3. **Audit the result.** `python3 tools/audit_pins.py --python` and
   `python3 tools/audit_pins.py --node`. A new advisory is a blocker, not a note.
4. **Install from the new pin** into a fresh venv and run the suite:
   `bash tools/pins/install.sh --full`, then `.venv/bin/pytest -q`, `ruff`, `mypy --strict`.
   For a runtime-only move, `--full --runtime` is the image's set.
5. **Record the evidence.** In the PR and in
   [docs/evidence/dependencies/pins.md](evidence/dependencies/pins.md): the old and new
   version, the command that produced the lock, the audit verdict with its date, the test
   result, and any advisory accepted with its reason. Update the "last audited" date in §2;
   a date that stops moving is the signal that the audit stopped running.
6. **Commit the lock with the declaration.** A commit that changes one without the other
   fails step 5 of `tools/ci.sh`, and that is deliberate.

**Moving a base image** is the same shape: resolve the tag with
`docker buildx imagetools inspect <tag>`, change the `FROM` line and its tag comment in one
commit, and add a row to the evidence doc with the old and new digest. No pin moves by
typing a version from memory; every one is resolved by a tool and recorded.

**Cadence, for when nothing is moving.** There is no Dependabot or Renovate here, so the
audit does not run on its own. Re-run `tools/audit_pins.py --python` and `--node` at each
release, and at least once a month, and move the §2 date. A pin that nobody has touched in
a year is not a stable pin; it is an untouched one.

**Node overrides.** `pnpm.overrides` in `package.json` is the only supported way to move a
pin without editing another package's declaration. It carries `postcss`, `next` and the
vulnerable `minimatch` range today. It is reviewed like a direct dependency: the advisory
it clears is quoted, and the frozen lock check plus a web build is the proof. A range
selector is used for `minimatch` so the patched 3.x and 5.x resolutions are left alone.

Who reviews: any dependency change is reviewed like code — CODEOWNERS for the touched
scope, and for a runtime dependency in the API image or anything that reaches keys, funds
or the ledger, the maintainer's explicit approval in the PR thread (the same rule as a
money-path change). A security-motivated bump (an advisory
whose fix version moved) is merged ahead of feature work rather than queued behind it.

What a reviewer checks: the version diff is explainable; no new package was added that
signs or custodies funds without an ADR (see §4); the audit verdict is quoted with a date
rather than asserted; and the lock diff matches the declaration diff.

## 4. Packages that sign or custody funds

Checked at the time of writing: neither lock contains a transaction-signing, wallet or key
custody library. The Python set is HTTP, ASGI, ORM, migration, validation and test
tooling; the Node set is Next/React, the graph view, Electron and the test tooling. The
executor's signer is a protocol in `services/executor/src/ezchain_executor/signing.py`
that the caller supplies, so the platform carries no vendor signer SDK
([SECURITY §1](SECURITY.md)).

A dependency that signs or custodies funds — a chain SDK that can produce a signature, a
keystore, a hardware-wallet library, a custody API client — needs an ADR in
[DECISIONS.md](DECISIONS.md) before it is added, per the issue that asked for this work.
The test `test_the_pinned_set_carries_no_signer_or_custody_library` fails when such a
package appears in a lock without that decision being recorded in this document.

## 5. Reproducible build — two clean builds, one digest

Pins and hashes make the *inputs* exact. The release claim is stronger: two independent
clean environments must turn those inputs into the same artifact. The drill compares a
digest of each clean install; the two environments share nothing but the committed locks,
so a stale lock or a constraint that resolves differently produces two different digests.

```bash
# Two independent clean environments, each installing only from the lock.
for n in 1 2; do
  venv="/tmp/ezchain-repro-$n"
  python3.11 -m venv "$venv"
  "$venv/bin/pip" install --require-hashes --no-cache-dir --disable-pip-version-check \
    -r requirements.lock
  "$venv/bin/pip" freeze --all | LC_ALL=C sort > "/tmp/ezchain-repro-$n.freeze"
  sha256sum "/tmp/ezchain-repro-$n.freeze"
done
diff /tmp/ezchain-repro-1.freeze /tmp/ezchain-repro-2.freeze   # no output == identical
```

The artifact digest is the sha256 of the sorted `pip freeze` for each install. Repeating
the drill with `requirements-runtime.lock` covers the API image's set. The offline path
makes the same comparison without a network on the second run: fill a wheelhouse with
`bash tools/pins/install.sh --wheelhouse DIR`, hash the sorted wheel filenames, then install
twice from it with `EZCHAIN_WHEELHOUSE=DIR bash tools/pins/install.sh --full --offline` and
compare the freeze digests. `check_pins.py --full` already proves one clean install resolves
to exactly the pins; running it from two separate empty environments is the "build twice"
proof. This is a release drill, not a default gate step: two fresh installs are heavy and a
small host will swap.

**Status: not verified on this host.** The two clean installs were not run here — the
machine has about 1.5 GB free RAM with heavy swap use. No "two builds, identical digests"
result is claimed. A release machine must run the drill above and record both digests in
[the evidence doc](evidence/dependencies/pins.md); until then this half of the acceptance is
open.

## 6. Reproducibility limits

- **One interpreter.** The locks are resolved for CPython 3.11. A 3.12 install would
  re-resolve and could pick different versions; `requires-python` is `>=3.11`, which is
  broader than what is pinned. Pin the interpreter in the image (it is `python:3.11-slim`)
  and in CI.
- **Hash coverage of platform wheels comes from the index.** `--require-hashes` proves a
  downloaded artifact matches a recorded digest; it does not prove the index served the
  artifact the maintainer intended.
- **No SBOM yet.** The issue asks for locks and an audit; an SBOM and an image scan are
  separate work and are not claimed here.
