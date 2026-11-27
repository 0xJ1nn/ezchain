# VAULT report — local vault key import and encryption

## Files written

- `services/executor/src/ezchain_executor/vault_local.py` — crypto-free local-vault tier
  (import validation, typed errors, versioned file schema, fail-closed store seam).
- `services/executor/src/ezchain_executor/__init__.py` — exports the new typed surface.
- `tests/executor/test_vault_local.py` — 38 new tests.
- `labs/company/runs/VAULT.pr.md` — pull-request text.
- `labs/company/runs/VAULT.report.md` — this report.

## Branch and commits

- Branch: `feat/vault-import`, base `origin/main` at
  `13095f851bb738af1535175a571134300a2e9be0`.
- Field commit (branch HEAD at verification):
  `c3a443c9dd077e8e92b4ef1b7617b2eb7d723a50`.
- Commit trailers: `Signed-off-by` and `Assisted-by: hy4-flash-free`.
- Nothing was pushed, promoted or merged. No remote operation was attempted.

## Dependency decision

**Blocked by the dependency rule.** `requirements-runtime.txt` has no crypto packages;
`requirements-runtime.lock` has none of `cryptography`, `argon2-cffi`, `mnemonic` or
`bip-utils`; the installed venv has none. `tools/check-licences.py` passes for the
current locks but has no declaration for the requested packages. No requirement file or
lock was changed, no unpinned dependency was added, and no AES/Argon2/BIP39 code was
written by hand.

Exact packages and versions the field needs (PyPI metadata checked 2026-11-27):

- `cryptography==50.0.2` — AES-256-GCM; Apache-2.0 OR BSD-3-Clause.
- `argon2-cffi==25.1.0` — Argon2id; MIT.
- `mnemonic==0.21` — BIP39 validation and seed; MIT.
- `bip-utils==2.13.0` — base58 and BIP44/SLIP-0010 derivation; MIT.
- Transitive: `argon2-cffi-bindings==26.2.0`, `cffi==2.1.1`, `pycparser==3.12`,
  `cbor2==6.3.0`, `coincurve==21.0.0`, `ecdsa==0.19.2`, `six==1.17.0`,
  `pycryptodome==4.6.0`, `pynacl==1.6.2`, `py-sr25519-bindings==0.2.4`,
  `ed25519-blake2b-fork==1.4.2`, `pytoniq-core-fork==0.1.48`, `bitarray==3.12.4`,
  `x25519==0.0.2`, `pycryptodomex==3.24.2`, `requests==2.34.2`.
- Licence-gate caveat: `py-sr25519-bindings`, `ed25519-blake2b-fork`,
  `pytoniq-core-fork` and `x25519` publish no accepted licence expression;
  `pycryptodomex` publishes a free-form one. The owner must supply reviewed
  declarations (the checker's fixture channel) before a hashed lock can be compiled.

## Counts

- Target command baseline before the change: 230 passed, 0 failed.
- Target command after the change: **268 passed, 0 failed** — **38 new tests**.
- Reason-code taxonomy guard: 12 passed, 0 failed; no code or taxonomy row added.
- New tests cover: Solana 32/64-byte base58 import, EVM hex import with and without
  prefix, published 12/24-word BIP39 vectors, both canonical derivation paths, explicit
  canonical path acceptance, invalid base58/hex/mnemonic shapes, derivation-path mismatch
  for both families, unknown format/family, format/family pairing, redacted receipts and
  errors, no creation/export/recovery operation, the loss disclosure, file-schema round
  trip, AAD excluding ciphertext, no plaintext in the serialised schema, corrupt JSON and
  field refusals, unknown version, bad KDF/cipher parameters, tampered ciphertext still
  refusing to unlock, fail-closed import/unlock/lock, cross-user handle refusal, and
  typed wrong-password/corrupt-file error classes.

## Verification commands and observed exit statuses

Run from this worktree with the brief's PYTHONPATH:

- `env -u PYTHONPATH PYTHONPATH="$P" /Users/0xj1n/Projects/ezchain/.venv/bin/pytest tests/core tests/executor -q` — exit 0, 268 passed.
- `... pytest tests/test_reason_codes.py -q` — exit 0, 12 passed.
- `... ruff check services/executor/src/ezchain_executor/vault_local.py services/executor/src/ezchain_executor/__init__.py tests/executor/test_vault_local.py` — exit 0.
- `... ruff format --check <same three files>` — exit 0.
- `... mypy services/executor/src/ezchain_executor/vault_local.py tests/executor/test_vault_local.py` — exit 0.
- `node tools/check-secrets.mjs` — exit 0 (`secrets: ok`).
- `... python tools/check-licences.py` — exit 0 (`licences: ok`, three documented notes).
- `node tools/check-links.mjs` — exit 0 (`links: ok`).
- `node tools/check-wireframe.mjs` — exit 0 (`wireframe: ok (32 pages, 32 files)`).
- `bash tools/ci.sh` — not verified here: the worktree has no `.venv`, so the gate would
  fall back to host tools and cannot evidence this diff. The targeted checks above are
  the evidence.

## Anything not done and why

- No encryption at rest, Argon2id KDF, AES-256-GCM, authenticated header verification,
  random salt/nonce generation or 0600 file write: all blocked by the missing pins.
- No BIP39 checksum/wordlist validation, seed generation or derived public address and
  fingerprint; therefore no real `KeyHandle` can be produced. Only mnemonic shape and
  derivation-path binding are implemented.
- No wrong-password or tampered-ciphertext *authentication* tests, and no round-trip or
  on-disk-plaintext test: they require a cipher. The suite instead asserts the typed
  errors exist and that the store refuses until pins land.
- No UI, no network, no CLI, no `docs/DECISIONS.md` change and no reason-code change:
  the brief excludes them and the dependency gate blocks the encrypted tier.

## Scope check

Only the three Python files above plus these two run reports were added or changed. No
new dependency declaration, no vendored crypto, no generated file, no unrelated refactor
and no test fixture with a real key was introduced.
