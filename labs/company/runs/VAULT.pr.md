# Local vault key import: formats and typed seams, with an explicit crypto block

## Problem

The executor has the custody seam (`KeyHandle`, `Vault`, `SignerTransport`, typed
refusals) but no local-vault tier: no import formats, no encrypted file, no unlock.
ADR-0026 and LIVE-EXECUTION.md §2 name Argon2id + AES-256-GCM; ADR-0040 says the key
belongs to the user, is imported at runtime, and never appears in a log, error or
serialised object.

The dependency rule in the brief is decisive. `requirements-runtime.txt` and
`requirements-runtime.lock` contain none of `cryptography`, `argon2-cffi`, `mnemonic`
or `bip-utils`; the installed test venv contains none either. `tools/check-licences.py`
is green for the current locks but has no declaration for any of them. Writing AES,
Argon2 or BIP39 by hand, or adding unpinned packages, is forbidden. So the encrypted
half is **blocked**, and this change delivers the part that needs no crypto.

## Change

- `services/executor/src/ezchain_executor/vault_local.py` — the crypto-free local-vault
  tier:
  - All three import formats are first class: Solana base58 secret key (32 or 64 bytes,
    base58 decoded by a small codec), EVM hex private key (32 bytes, optional `0x`), and
    BIP39 mnemonic (12 or 24 words, shape only). Raw key formats are bound to their
    chain family.
  - Per-chain derivation paths are canonical: Solana `m/44'/501'/0'/0'`, EVM
    `m/44'/60'/0'/0/0`. An omitted path takes the family's default; a supplied path that
    differs refuses with `DerivationPathMismatch`.
  - `ImportReceipt` carries only public metadata and *lengths* — no key, seed, mnemonic
    or passphrase field, and a redacted repr. Errors are fixed sentences that never echo
    the rejected input.
  - Typed refusals: import-format/family/key errors, `WrongVaultPassword`,
    `CorruptVaultFile`, `UnsupportedVaultVersion`, `VaultHandleOwnerMismatch` and
    `VaultCryptoUnavailable`, all with messages that carry no secret.
  - The versioned `vault.json` record schema from LIVE-EXECUTION.md §2: Argon2id
    parameters, AES-256-GCM nonce/ciphertext, strict base64 and field decoding, plus
    `authenticated_header()` — the canonical AAD a cipher will use for version, KDF and
    public header fields (never the ciphertext).
  - `LocalVaultStore` protocol and `UnavailableLocalVaultStore`: validation and owner
    binding still run, then every import/unlock/lock refuses with the exact pin request.
    No file is written. Unlock is typed to return a `KeyHandle`, never a key.
  - `KEY_IMPORT_NOTICE` says plainly that the platform does not create, back up or
    recover a key and that losing the passphrase or file loses the key. No wallet
    creation, seed generation or recovery kit exists here.
- `services/executor/src/ezchain_executor/__init__.py` — exports the new typed surface.
- `tests/executor/test_vault_local.py` — 38 tests over published BIP39 vectors,
  Hardhat's well-known development key and obvious byte patterns. No real key material.

Invariant I4 is unchanged: keys stay inside the executor. The signing path and
`docs/DECISIONS.md` are untouched. No UI, no network, no database.

## Dependency decision (blocking the encrypted half)

The field needs these exact reviewed pins before any encryption ships:

| Package | Version | Licence from PyPI metadata | Role |
|---|---|---|---|
| `cryptography` | `50.0.2` | Apache-2.0 OR BSD-3-Clause | AES-256-GCM |
| `argon2-cffi` | `25.1.0` | MIT | Argon2id KDF |
| `mnemonic` | `0.21` | MIT | BIP39 wordlist/checksum and seed |
| `bip-utils` | `2.13.0` | MIT | base58 and BIP44/SLIP-0010 derivation |

Transitive pins that the resolver will add: `argon2-cffi-bindings==26.2.0`,
`cffi==2.1.1`, `pycparser==3.12`, `cbor2==6.3.0`, `coincurve==21.0.0`, `ecdsa==0.19.2`,
`six==1.17.0`, `pycryptodome==4.6.0`, `pynacl==1.6.2`, `py-sr25519-bindings==0.2.4`,
`ed25519-blake2b-fork==1.4.2`, `pytoniq-core-fork==0.1.48`, `bitarray==3.12.4`,
`x25519==0.0.2`, `pycryptodomex==3.24.2`, `requests==2.34.2`. Four of these
(`py-sr25519-bindings`, `ed25519-blake2b-fork`, `pytoniq-core-fork`, `x25519`) have no
accepted licence declaration in PyPI metadata; `pycryptodomex` declares a free-form
licence. The licence gate will need reviewed fixtures before a hashed runtime lock can
be compiled. Nothing was added to any requirement file or lock in this change.

## Not done, and why

Encryption, decryption, Argon2id derivation, BIP39 seed/address/fingerprint derivation,
real `KeyHandle` production, authenticated-header verification, wrong-password
detection, tampered-ciphertext rejection, 0600 file writes, unlock round trips and
"no plaintext on disk" tests are all blocked by the missing pins. They are not faked:
`UnavailableLocalVaultStore` refuses instead of shipping a stand-in cipher. The
test suite deliberately does not claim those behaviours pass.

## Verification

- `pytest tests/core tests/executor -q`: 268 passed, 0 failed (baseline 230; 38 new).
- `pytest tests/test_reason_codes.py -q`: 12 passed, 0 failed. No new reason code.
- `ruff check` and `ruff format --check` on the three touched Python files: clean.
- `mypy` on the new module and its tests: clean.
- `node tools/check-secrets.mjs`: ok. `python tools/check-licences.py`: ok.
- `node tools/check-links.mjs`: ok. `node tools/check-wireframe.mjs`: ok (32 pages).
