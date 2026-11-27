"""Local vault encryption: a sealed record round trips and every refusal is typed.

The keys here are obvious byte patterns and published BIP39 vectors; none can spend.
Cryptography uses the reviewed libraries through the executor's own module: the tests
drive the encrypted store, not a hand-rolled cipher. Wrong passphrases, tampered bytes,
changed headers, unknown versions, malformed files and missing pins all fail closed with
fixed messages that carry no secret material.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from ezchain_core import CUSTODY_LOCAL_VAULT, KeyHandle, is_fingerprint
from ezchain_executor import (
    CORRUPTED_VAULT_MESSAGE,
    DERIVATION_PATHS,
    EVM_DERIVATION_PATH,
    FAMILY_EVM,
    FAMILY_SVM,
    IMPORT_BIP39_MNEMONIC,
    IMPORT_EVM_HEX,
    IMPORT_SOLANA_BASE58,
    SOLANA_DERIVATION_PATH,
    WRONG_PASSWORD_MESSAGE,
    CorruptVaultFile,
    EncryptedLocalVaultStore,
    InvalidMnemonicShape,
    UnsupportedVaultVersion,
    VaultCryptoUnavailable,
    VaultHandleOwnerMismatch,
    VaultImportError,
    WrongVaultPassword,
    decode_vault_file,
    encode_vault_file,
)
from ezchain_executor import vault_local as local

BIP39_12 = " ".join(["abandon"] * 11 + ["about"])
BIP39_BAD_CHECKSUM = " ".join(["abandon"] * 12)

# Obvious test-only values: a repeated byte pattern, a published vector and a fake address.
SECRET_32 = b"\x01" * 32
EVM_KEY = "0x" + "11" * 32
FINGERPRINT = hashlib.sha256(b"public-only-test-material").hexdigest()
ADDRESS = "Wallet11111111111111111111111111111111111111"
OWNER = "user:alice"
OTHER = "user:bob"
PASSPHRASE = "this-is-a-test-vault-passphrase"
OTHER_PASSPHRASE = "a-different-test-vault-passphrase"


def b58encode(raw: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = int.from_bytes(raw, "big")
    text = ""
    while number:
        number, digit = divmod(number, 58)
        text = alphabet[digit] + text
    return "1" * (len(raw) - len(raw.lstrip(b"\x00"))) + text


def store(path: Path) -> EncryptedLocalVaultStore:
    return EncryptedLocalVaultStore(path)


def import_key(
    vault: EncryptedLocalVaultStore,
    material: str,
    *,
    source_format: str,
    family: str,
    owner: str = OWNER,
    passphrase: str = PASSPHRASE,
    address: str = ADDRESS,
    fingerprint: str = FINGERPRINT,
    derivation_path: str | None = None,
    mnemonic_passphrase: str | None = None,
) -> KeyHandle:
    return vault.import_key(
        material,
        source_format=source_format,
        family=family,
        owner=owner,
        passphrase=passphrase,
        address=address,
        fingerprint=fingerprint,
        derivation_path=derivation_path,
        mnemonic_passphrase=mnemonic_passphrase,
    )


def sole_record(path: Path):  # type: ignore[no-untyped-def]
    records = decode_vault_file(path.read_bytes())
    assert len(records) == 1
    return records[0]


# --- Round trip: a sealed record unlocks a public handle ----------------------


def test_import_and_unlock_round_trip_returns_only_a_public_handle(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    vault = store(path)
    handle = import_key(vault, EVM_KEY, source_format=IMPORT_EVM_HEX, family=FAMILY_EVM)

    assert handle.owner == OWNER
    assert handle.custody == CUSTODY_LOCAL_VAULT
    assert handle.chain == local.CHAIN_BY_FAMILY[FAMILY_EVM]
    assert handle.address == ADDRESS
    assert is_fingerprint(handle.fingerprint)
    assert handle.fingerprint == FINGERPRINT
    assert vault.unlock(handle, owner=OWNER, passphrase=PASSPHRASE) == handle

    record = sole_record(path)
    assert record.family == FAMILY_EVM
    assert record.address == ADDRESS
    assert record.cipher.ciphertext != EVM_KEY.encode()
    assert record.kdf.algorithm == "argon2id"
    assert record.cipher.algorithm == "aes-256-gcm"


def test_solana_base58_import_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    vault = store(path)
    handle = import_key(
        vault,
        b58encode(SECRET_32),
        source_format=IMPORT_SOLANA_BASE58,
        family=FAMILY_SVM,
    )
    assert handle.chain == local.CHAIN_BY_FAMILY[FAMILY_SVM]
    assert vault.unlock(handle, owner=OWNER, passphrase=PASSPHRASE) == handle


def test_a_bip39_mnemonic_import_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    vault = store(path)
    handle = import_key(
        vault,
        BIP39_12,
        source_format=IMPORT_BIP39_MNEMONIC,
        family=FAMILY_SVM,
        mnemonic_passphrase="an-optional-bip39-passphrase",
    )
    assert vault.unlock(handle, owner=OWNER, passphrase=PASSPHRASE) == handle


def test_the_sealed_file_carries_no_plaintext_key_or_passphrase(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    import_key(store(path), EVM_KEY, source_format=IMPORT_EVM_HEX, family=FAMILY_EVM)
    data = path.read_bytes()
    assert EVM_KEY[2:].encode() not in data
    assert PASSPHRASE.encode() not in data
    assert path.stat().st_mode & 0o077 == 0


def test_open_round_trips_the_secret_bytes_and_checks_the_header(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    import_key(store(path), EVM_KEY, source_format=IMPORT_EVM_HEX, family=FAMILY_EVM)
    record = sole_record(path)

    assert local._open_secret(record, PASSPHRASE) == bytes.fromhex(EVM_KEY[2:])
    moved = replace(record, address="Wallet22222222222222222222222222222222222222")
    with pytest.raises(WrongVaultPassword) as exc:
        local._open_secret(moved, PASSPHRASE)
    assert str(exc.value) == WRONG_PASSWORD_MESSAGE


def test_the_store_holds_no_plaintext_secret(tmp_path: Path) -> None:
    vault = store(tmp_path / "vault.json")
    import_key(vault, EVM_KEY, source_format=IMPORT_EVM_HEX, family=FAMILY_EVM)
    assert not any(hasattr(vault, name) for name in ("key", "secret", "seed", "passphrase"))
    for value in vars(vault).values():
        assert EVM_KEY[2:] not in repr(value)


# --- Fail closed on wrong credentials, tampering and malformed records --------


def test_a_wrong_passphrase_refuses_with_a_fixed_message(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    vault = store(path)
    handle = import_key(vault, EVM_KEY, source_format=IMPORT_EVM_HEX, family=FAMILY_EVM)

    with pytest.raises(WrongVaultPassword) as exc:
        vault.unlock(handle, owner=OWNER, passphrase=OTHER_PASSPHRASE)
    assert str(exc.value) == WRONG_PASSWORD_MESSAGE
    assert PASSPHRASE not in str(exc.value)
    assert OTHER_PASSPHRASE not in str(exc.value)


def test_tampered_ciphertext_refuses_without_echoing_bytes(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    vault = store(path)
    handle = import_key(vault, EVM_KEY, source_format=IMPORT_EVM_HEX, family=FAMILY_EVM)
    record = sole_record(path)
    tampered = replace(
        record,
        cipher=replace(
            record.cipher,
            ciphertext=bytes([record.cipher.ciphertext[0] ^ 1]) + record.cipher.ciphertext[1:],
        ),
    )
    path.write_bytes(encode_vault_file((tampered,)))

    with pytest.raises(WrongVaultPassword) as exc:
        vault.unlock(handle, owner=OWNER, passphrase=PASSPHRASE)
    assert str(exc.value) == WRONG_PASSWORD_MESSAGE
    assert repr(tampered.cipher.ciphertext) not in str(exc.value)


def test_a_changed_authenticated_header_refuses(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    vault = store(path)
    handle = import_key(vault, EVM_KEY, source_format=IMPORT_EVM_HEX, family=FAMILY_EVM)
    record = sole_record(path)
    # The salt is inside the AAD and changes the KEK; the handle's public fields do not
    # cross-check it, so the only possible verdict is an authentication failure.
    moved = replace(record, kdf=replace(record.kdf, salt=b"\x99" * 16))
    path.write_bytes(encode_vault_file((moved,)))

    with pytest.raises(WrongVaultPassword) as exc:
        vault.unlock(handle, owner=OWNER, passphrase=PASSPHRASE)
    assert str(exc.value) == WRONG_PASSWORD_MESSAGE


def test_an_unknown_version_refuses_before_any_crypto(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    vault = store(path)
    handle = import_key(vault, EVM_KEY, source_format=IMPORT_EVM_HEX, family=FAMILY_EVM)
    # Change the serialised version: building a new VaultRecord would refuse it at
    # construction, so this proves the reader refuses an unsupported on-disk version.
    path.write_bytes(path.read_bytes().replace(b'"version":1', b'"version":2'))

    with pytest.raises(UnsupportedVaultVersion):
        vault.unlock(handle, owner=OWNER, passphrase=PASSPHRASE)


@pytest.mark.parametrize("data", [b"not-json", b'{"records": "no"}', b'{"records": [1]}'])
def test_malformed_files_fail_closed(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "vault.json"
    path.write_bytes(data)
    handle = KeyHandle(
        handle_id="h-test",
        owner=OWNER,
        custody=CUSTODY_LOCAL_VAULT,  # type: ignore[arg-type]
        chain="evm",
        address=ADDRESS,
        fingerprint=FINGERPRINT,
    )
    with pytest.raises(CorruptVaultFile):
        store(path).unlock(handle, owner=OWNER, passphrase=PASSPHRASE)


def test_a_missing_crypto_pin_fails_closed_with_a_fixed_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "vault.json"
    vault = store(path)
    for name in (
        "cryptography",
        "cryptography.exceptions",
        "cryptography.hazmat.primitives.ciphers.aead",
        "argon2",
        "argon2.low_level",
    ):
        monkeypatch.setitem(sys.modules, name, None)

    with pytest.raises(VaultCryptoUnavailable) as exc:
        import_key(vault, EVM_KEY, source_format=IMPORT_EVM_HEX, family=FAMILY_EVM)
    assert "cryptography" in str(exc.value)
    assert EVM_KEY[2:] not in str(exc.value)
    assert not path.exists()


def test_a_malformed_file_with_no_crypto_pin_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "vault.json"
    path.write_bytes(b"not-json")
    handle = KeyHandle(
        handle_id="h-test",
        owner=OWNER,
        custody=CUSTODY_LOCAL_VAULT,  # type: ignore[arg-type]
        chain="evm",
        address=ADDRESS,
        fingerprint=FINGERPRINT,
    )
    monkeypatch.setitem(sys.modules, "cryptography", None)
    with pytest.raises(CorruptVaultFile):
        store(path).unlock(handle, owner=OWNER, passphrase=PASSPHRASE)


# --- Validation stays first class and no second boundary appears --------------


def test_a_noncanonical_derivation_path_refuses_before_any_crypto(tmp_path: Path) -> None:
    with pytest.raises(VaultImportError):
        import_key(
            store(tmp_path / "vault.json"),
            EVM_KEY,
            source_format=IMPORT_EVM_HEX,
            family=FAMILY_EVM,
            derivation_path=SOLANA_DERIVATION_PATH,
        )


def test_both_canonical_paths_are_accepted(tmp_path: Path) -> None:
    assert DERIVATION_PATHS[FAMILY_SVM] == SOLANA_DERIVATION_PATH
    assert DERIVATION_PATHS[FAMILY_EVM] == EVM_DERIVATION_PATH
    handle = import_key(
        store(tmp_path / "vault.json"),
        EVM_KEY,
        source_format=IMPORT_EVM_HEX,
        family=FAMILY_EVM,
        derivation_path=EVM_DERIVATION_PATH,
    )
    assert handle.fingerprint == FINGERPRINT


def test_missing_public_facts_refuse_before_any_crypto(tmp_path: Path) -> None:
    vault = store(tmp_path / "vault.json")
    with pytest.raises(VaultImportError):
        import_key(
            vault,
            EVM_KEY,
            source_format=IMPORT_EVM_HEX,
            family=FAMILY_EVM,
            address="",
        )
    with pytest.raises(VaultImportError):
        import_key(
            vault,
            EVM_KEY,
            source_format=IMPORT_EVM_HEX,
            family=FAMILY_EVM,
            fingerprint="not-a-hash",
        )
    assert not (tmp_path / "vault.json").exists()


def test_a_mnemonic_with_a_bad_checksum_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InvalidMnemonicShape):
        import_key(
            store(tmp_path / "vault.json"),
            BIP39_BAD_CHECKSUM,
            source_format=IMPORT_BIP39_MNEMONIC,
            family=FAMILY_SVM,
        )


def test_the_optional_bip39_passphrase_changes_the_sealed_secret(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    import_key(
        store(first),
        BIP39_12,
        source_format=IMPORT_BIP39_MNEMONIC,
        family=FAMILY_SVM,
        mnemonic_passphrase="first",
    )
    import_key(
        store(second),
        BIP39_12,
        source_format=IMPORT_BIP39_MNEMONIC,
        family=FAMILY_SVM,
        mnemonic_passphrase="second",
    )
    assert sole_record(first).cipher.ciphertext != sole_record(second).cipher.ciphertext


def test_another_owners_handle_refuses_before_a_file_read(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    vault = store(path)
    handle = import_key(vault, EVM_KEY, source_format=IMPORT_EVM_HEX, family=FAMILY_EVM)

    with pytest.raises(VaultHandleOwnerMismatch):
        vault.unlock(handle, owner=OTHER, passphrase=PASSPHRASE)
    assert str(handle)  # the handle itself is public and safe to print


def test_lock_clears_the_unlocked_handles(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    vault = store(path)
    handle = import_key(vault, EVM_KEY, source_format=IMPORT_EVM_HEX, family=FAMILY_EVM)
    vault.unlock(handle, owner=OWNER, passphrase=PASSPHRASE)
    assert handle.handle_id in vault.unlocked_handles(OWNER)

    vault.lock(OWNER)
    assert vault.unlocked_handles(OWNER) == ()


def test_two_keys_are_sealed_under_independent_parameters(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    vault = store(path)
    first = import_key(vault, EVM_KEY, source_format=IMPORT_EVM_HEX, family=FAMILY_EVM)
    second = import_key(
        vault,
        b58encode(SECRET_32),
        source_format=IMPORT_SOLANA_BASE58,
        family=FAMILY_SVM,
    )
    records = decode_vault_file(path.read_bytes())
    assert len(records) == 2
    assert records[0].kdf.salt != records[1].kdf.salt
    assert records[0].cipher.nonce != records[1].cipher.nonce
    assert vault.unlock(first, owner=OWNER, passphrase=PASSPHRASE) == first
    assert vault.unlock(second, owner=OWNER, passphrase=PASSPHRASE) == second


def test_the_fixed_error_messages_name_no_secret() -> None:
    assert PASSPHRASE not in WRONG_PASSWORD_MESSAGE
    assert PASSPHRASE not in CORRUPTED_VAULT_MESSAGE
    assert issubclass(WrongVaultPassword, local.VaultStoreError)
    assert issubclass(CorruptVaultFile, local.VaultStoreError)
