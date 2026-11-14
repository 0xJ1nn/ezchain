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

## 1. What is proven, and what is not

Proven on this machine (2026-11-14, macOS arm64, CPython 3.11.18, Node 26.13.0,
pnpm 9.15.9):

- `tools/check_pins.py --fast` — every pin is an exact `==` version, every Python pin
  carries hashes, every Node resolution carries integrity, and every declared range is
  still satisfied by its pin. This is step 6 of `tools/ci.sh`; it needs no network.
- `tools/check_pins.py --full` — a fresh venv installs `requirements.lock` with
  `--require-hashes` and `pip freeze` matches the pins exactly: 43 pinned, 43 installed.
- `tools/check_pins.py --full --runtime` — same for `requirements-runtime.lock`:
  31 pinned, 31 installed.
- `tools/check_pins.py --full --offline` with `EZCHAIN_WHEELHOUSE` set — the same
  install from a local wheelhouse with `--no-index`: 43 from a 43-wheel directory, and
  31 from a 31-wheel directory for the runtime set. The wheelhouse digests are in
  [docs/evidence/dependencies/pins.md](evidence/dependencies/pins.md).
- `tools/audit_pins.py --python` against the live advisory service, and
  `tools/audit_pins.py --node` against the npm registry. Results and dates are in
  [docs/evidence/dependencies/pins.md](evidence/dependencies/pins.md).
- `bash tools/ci.sh` with step 6 in place.

Not proven here, and not claimed:

- **Another platform.** The full installs ran on macOS arm64. The locks carry hashes for
  every published wheel of each version, so a Linux install resolves from the same pins,
  but no Docker daemon exists on this host and the image build was not run. The Linux
  install is unverified until someone runs it.
- **A cold clone.** The offline path started from a wheelhouse this host produced in the
  same session. A wheelhouse built on another machine, or with a stale lock, is a
  different claim; `tools/pins/install.sh --wheelhouse DIR` records the lock digest so a
  mismatch is visible, but the drill has not been repeated from a cold clone.
- **`services/ingest[firehose]`.** Its pin is a git tag
  (`pumpfun-firehose @ git+https://…@v0.5.0`), which has no hashes to verify. It is an
  optional extra, absent from every lock, and installing it requires a network and the
  tag to exist. Nothing in this change pins it; that gap is named rather than hidden.
- **`keyring`.** `requirements-keyring.lock` is generated and audited, but no deployment
  installs it, so it is unexercised outside the resolver.

## 2. The audit step

```bash
python3 tools/audit_pins.py --python    # the three Python locks, via pip-audit (OSV/PyPI)
python3 tools/audit_pins.py --node      # the pnpm workspace, via the npm registry
python3 tools/audit_pins.py --report saved-pip-audit.json   # judge a report offline
bash tools/pins/install.sh --audit      # the Python path through the install wrapper
```

It **fails** — exit 1 — on any advisory whose id is not allowlisted, naming the pin, the
id and the fixed version. Exit 2 means the audit could not run (tool absent, no network,
unreadable report), and the script says so; exit 2 is never treated as a pass.

`tools/pins/audit-allowlist.txt` is the only way an advisory stops failing the build. Each
line needs an id and a reason; an entry without a reason is itself an error, so the file
cannot quietly become a mute button. It is empty today.

`pip-audit` is not a pinned dependency: it is an audit tool, and pinning it would add a
resolver-shaped dependency to the thing being audited. Install it where the audit runs:

```bash
pip install 'pip-audit>=2,<3'
```

Offline, the verdict is the same code path: run `pip-audit --format json -o report.json`
where a network exists, copy the report, and judge it with `--report`.

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
   result, and any advisory accepted with its reason.
6. **Commit the lock with the declaration.** A commit that changes one without the other
   fails step 6 of `tools/ci.sh`, and that is deliberate.

Who reviews: any dependency change is reviewed like code — CODEOWNERS for the touched
scope, and for a runtime dependency in the API image or anything that reaches keys, funds
or the ledger, the maintainer's explicit approval in the PR thread (the same rule as a
money-path change, [WORKFLOW §3](WORKFLOW.md)). A security-motivated bump (an advisory
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

## 5. Reproducibility limits

- **One interpreter.** The locks are resolved for CPython 3.11. A 3.12 install would
  re-resolve and could pick different versions; `requires-python` is `>=3.11`, which is
  broader than what is pinned. Pin the interpreter in the image (it is `python:3.11-slim`)
  and in CI.
- **Hash coverage of platform wheels comes from the index.** `--require-hashes` proves a
  downloaded artifact matches a recorded digest; it does not prove the index served the
  artifact the maintainer intended.
- **No SBOM yet.** The issue asks for locks and an audit; an SBOM and an image scan are
  separate work and are not claimed here.
