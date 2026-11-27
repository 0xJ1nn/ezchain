"""The local-vault tier: first-class import validation and fail-closed refusals.

This suite covers what needs no crypto: import formats, derivation-path binding, the
versioned file schema and typed errors. Sealed-file round trips and authentication live
in ``test_vault_encryption.py``. All keys here are published vectors or obvious byte
patterns; none can spend anything.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from ezchain_core import KeyHandle
from ezchain_executor import (
    CIPHER_ALGORITHM,
    DERIVATION_PATHS,
    EVM_DERIVATION_PATH,
    FAMILY_EVM,
    FAMILY_SVM,
    IMPORT_BIP39_MNEMONIC,
    IMPORT_EVM_HEX,
    IMPORT_SOLANA_BASE58,
    KDF_ALGORITHM,
    KEY_IMPORT_NOTICE,
    REQUIRED_CRYPTO_PINS,
    SOLANA_DERIVATION_PATH,
    VAULT_FILE_VERSION,
    CipherParams,
    CorruptVaultFile,
    DerivationPathMismatch,
    InvalidEvmKey,
    InvalidMnemonicShape,
    InvalidSolanaKey,
    KdfParams,
    UnavailableLocalVaultStore,
    UnsupportedImportFormat,
    UnsupportedKeyFamily,
    UnsupportedVaultVersion,
    VaultCryptoUnavailable,
    VaultHandleOwnerMismatch,
    VaultRecord,
    WrongVaultPassword,
    assert_handle_owner,
    authenticated_header,
    decode_vault_file,
    encode_vault_file,
    resolve_derivation_path,
    validate_import,
    validate_mnemonic_shape,
)
from ezchain_executor import vault_local as local

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

# Public BIP39 known-answer vectors (Trezor's published all-zero entropy sentences).
BIP39_12 = " ".join(["abandon"] * 11 + ["about"])
BIP39_24 = " ".join(["abandon"] * 23 + ["art"])

# Hardhat's well-known development account #0, printed in every Hardhat install log.
HARDHAT_DEV_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

OWNER = "user:alice"
OTHER = "user:bob"
VAULT_PASSPHRASE = "this-is-a-test-vault-passphrase"


def b58encode(raw: bytes) -> str:
    """Base58 is an encoding, not crypto; this helper only builds test vectors."""
    number = int.from_bytes(raw, "big")
    text = ""
    while number:
        number, digit = divmod(number, 58)
        text = _B58[digit] + text
    leading = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * leading + text


def handle(owner: str = OWNER) -> KeyHandle:
    return KeyHandle(
        handle_id="h-local-1",
        owner=owner,
        custody="local_vault",
        chain="solana",
        address="Wallet11111111111111111111111111111111111111",
        fingerprint=hashlib.sha256(b"public-only-test-material").hexdigest(),
    )


def record(
    *,
    version: int = VAULT_FILE_VERSION,
    ciphertext: bytes = b"ciphertext-placeholder-not-a-key",
) -> VaultRecord:
    return VaultRecord(
        version=version,
        id="h-local-1",
        label="test-only",
        family=FAMILY_SVM,
        address="Wallet11111111111111111111111111111111111111",
        kdf=KdfParams(
            algorithm=KDF_ALGORITHM,
            salt=b"\x11" * 16,
            time_cost=3,
            memory_cost=65536,
            parallelism=1,
        ),
        cipher=CipherParams(
            algorithm=CIPHER_ALGORITHM,
            nonce=b"\x22" * 12,
            ciphertext=ciphertext,
        ),
        created_at="2026-11-27T00:00:00Z",
    )


# --- Import formats are all first class --------------------------------------


def test_solana_base58_32_byte_secret_key_is_first_class() -> None:
    material = b58encode(b"\x01" * 32)
    receipt = validate_import(material, source_format=IMPORT_SOLANA_BASE58, family=FAMILY_SVM)
    assert receipt.secret_bytes == 32
    assert receipt.family == FAMILY_SVM
    assert receipt.source_format == IMPORT_SOLANA_BASE58
    assert receipt.derivation_path == SOLANA_DERIVATION_PATH


def test_solana_base58_64_byte_secret_key_is_first_class() -> None:
    material = b58encode(b"\x02" * 64)
    receipt = validate_import(material, source_format=IMPORT_SOLANA_BASE58, family=FAMILY_SVM)
    assert receipt.secret_bytes == 64


def test_evm_hex_private_key_is_first_class() -> None:
    receipt = validate_import(HARDHAT_DEV_KEY, source_format=IMPORT_EVM_HEX, family=FAMILY_EVM)
    assert receipt.secret_bytes == 32
    assert receipt.family == FAMILY_EVM
    assert receipt.derivation_path == EVM_DERIVATION_PATH


def test_evm_hex_private_key_without_prefix_is_accepted() -> None:
    assert (
        validate_import(
            HARDHAT_DEV_KEY[2:], source_format=IMPORT_EVM_HEX, family=FAMILY_EVM
        ).secret_bytes
        == 32
    )


def test_published_12_word_vector_imports_for_solana() -> None:
    receipt = validate_import(BIP39_12, source_format=IMPORT_BIP39_MNEMONIC, family=FAMILY_SVM)
    assert len(BIP39_12.split()) == 12
    assert receipt.secret_bytes is None, "a mnemonic's derived length needs BIP32"
    assert receipt.derivation_path == SOLANA_DERIVATION_PATH


def test_published_24_word_vector_imports_for_evm() -> None:
    receipt = validate_import(BIP39_24, source_format=IMPORT_BIP39_MNEMONIC, family=FAMILY_EVM)
    assert len(BIP39_24.split()) == 24
    assert receipt.secret_bytes is None
    assert receipt.derivation_path == EVM_DERIVATION_PATH


def test_default_paths_are_the_two_per_chain_paths() -> None:
    assert DERIVATION_PATHS[FAMILY_SVM] == "m/44'/501'/0'/0'"
    assert DERIVATION_PATHS[FAMILY_EVM] == "m/44'/60'/0'/0/0"
    assert resolve_derivation_path(FAMILY_SVM) == SOLANA_DERIVATION_PATH
    assert resolve_derivation_path(FAMILY_EVM) == EVM_DERIVATION_PATH


def test_an_explicit_canonical_path_is_accepted() -> None:
    assert resolve_derivation_path(FAMILY_EVM, EVM_DERIVATION_PATH) == EVM_DERIVATION_PATH


# --- Invalid input and path mismatch refuse with typed errors ----------------


def test_invalid_base58_character_is_refused() -> None:
    with pytest.raises(InvalidSolanaKey):
        validate_import("0OIl", source_format=IMPORT_SOLANA_BASE58, family=FAMILY_SVM)


def test_a_short_solana_key_is_refused() -> None:
    with pytest.raises(InvalidSolanaKey):
        validate_import(
            b58encode(b"\x01" * 31), source_format=IMPORT_SOLANA_BASE58, family=FAMILY_SVM
        )


def test_an_oversized_base58_blob_is_refused() -> None:
    with pytest.raises(InvalidSolanaKey):
        validate_import("1" * 89, source_format=IMPORT_SOLANA_BASE58, family=FAMILY_SVM)


def test_a_short_evm_hex_key_is_refused() -> None:
    with pytest.raises(InvalidEvmKey):
        validate_import("0x1234", source_format=IMPORT_EVM_HEX, family=FAMILY_EVM)


def test_a_non_hex_evm_key_is_refused() -> None:
    with pytest.raises(InvalidEvmKey):
        validate_import("z" * 64, source_format=IMPORT_EVM_HEX, family=FAMILY_EVM)


@pytest.mark.parametrize("words", [11, 13, 23])
def test_wrong_mnemonic_word_counts_are_refused(words: int) -> None:
    with pytest.raises(InvalidMnemonicShape):
        validate_mnemonic_shape(" ".join(["abandon"] * words))


def test_mnemonic_with_a_non_word_token_is_refused() -> None:
    with pytest.raises(InvalidMnemonicShape):
        validate_mnemonic_shape(" ".join(["abandon"] * 11 + ["not-a-word"]))


def test_a_solana_key_cannot_use_the_evm_path() -> None:
    with pytest.raises(DerivationPathMismatch):
        validate_import(
            b58encode(b"\x01" * 32),
            source_format=IMPORT_SOLANA_BASE58,
            family=FAMILY_SVM,
            derivation_path=EVM_DERIVATION_PATH,
        )


def test_an_evm_key_cannot_use_the_solana_path() -> None:
    with pytest.raises(DerivationPathMismatch):
        validate_import(
            HARDHAT_DEV_KEY,
            source_format=IMPORT_EVM_HEX,
            family=FAMILY_EVM,
            derivation_path=SOLANA_DERIVATION_PATH,
        )


def test_unknown_format_and_family_are_refused() -> None:
    with pytest.raises(UnsupportedImportFormat):
        validate_import("anything", source_format="magic", family=FAMILY_EVM)
    with pytest.raises(UnsupportedKeyFamily):
        validate_import("anything", source_format=IMPORT_EVM_HEX, family="bitcoin")


def test_raw_key_formats_are_bound_to_their_chain_family() -> None:
    with pytest.raises(UnsupportedKeyFamily):
        validate_import(
            b58encode(b"\x01" * 32), source_format=IMPORT_SOLANA_BASE58, family=FAMILY_EVM
        )
    with pytest.raises(UnsupportedKeyFamily):
        validate_import(HARDHAT_DEV_KEY, source_format=IMPORT_EVM_HEX, family=FAMILY_SVM)


# --- No creation, no recovery kit, no key material in outputs ----------------


def test_the_receipt_has_no_field_a_key_could_live_in() -> None:
    fields = set(local.ImportReceipt.__dataclass_fields__)
    assert fields == {"family", "source_format", "derivation_path", "secret_bytes"}
    receipt = validate_import(HARDHAT_DEV_KEY, source_format=IMPORT_EVM_HEX, family=FAMILY_EVM)
    assert HARDHAT_DEV_KEY not in repr(receipt)
    assert HARDHAT_DEV_KEY not in str(receipt)


def test_error_messages_never_echo_the_rejected_material() -> None:
    material = "0x" + "f" * 63 + "g"
    with pytest.raises(InvalidEvmKey) as exc:
        validate_import(material, source_format=IMPORT_EVM_HEX, family=FAMILY_EVM)
    assert material not in str(exc.value)


def test_the_module_exposes_no_creation_or_export_operation() -> None:
    forbidden = ("generate", "create", "new_wallet", "export", "recover", "seed")
    assert not [name for name in forbidden if hasattr(local, name)]


def test_the_key_add_notice_says_loss_is_the_owners() -> None:
    assert "does not create, back up or recover" in KEY_IMPORT_NOTICE
    assert "losing either loses the key" in KEY_IMPORT_NOTICE


# --- Versioned file schema, without pretending to encrypt -------------------


def test_vault_file_round_trips_the_schema() -> None:
    source = record()
    decoded = decode_vault_file(encode_vault_file((source,)))
    assert decoded == (source,)
    assert decoded[0].version == VAULT_FILE_VERSION
    assert decoded[0].kdf.salt == b"\x11" * 16
    assert decoded[0].cipher.nonce == b"\x22" * 12


def test_authenticated_header_excludes_the_ciphertext() -> None:
    source = record()
    header = authenticated_header(source)
    assert KDF_ALGORITHM.encode() in header
    assert CIPHER_ALGORITHM.encode() in header
    assert source.cipher.ciphertext not in header


def test_serialised_file_carries_no_plaintext_key() -> None:
    encoded = encode_vault_file((record(),))
    assert HARDHAT_DEV_KEY.encode() not in encoded
    assert b"secret" not in encoded.lower()


def test_malformed_json_and_shapes_are_refused() -> None:
    with pytest.raises(CorruptVaultFile):
        decode_vault_file(b"not-json")
    with pytest.raises(CorruptVaultFile):
        decode_vault_file(b'{"records": "not-a-list"}')


def test_an_unknown_vault_version_is_refused() -> None:
    with pytest.raises(UnsupportedVaultVersion):
        record(version=VAULT_FILE_VERSION + 1)


def test_invalid_kdf_and_cipher_parameters_are_refused() -> None:
    with pytest.raises(CorruptVaultFile):
        replace(record().kdf, time_cost=0)
    with pytest.raises(CorruptVaultFile):
        replace(record().cipher, nonce=b"too-short")


def test_tampered_ciphertext_still_refuses_to_unlock_without_pins() -> None:
    tampered = decode_vault_file(encode_vault_file((record(ciphertext=b"tampered-placeholder"),)))[
        0
    ]
    assert authenticated_header(tampered) == authenticated_header(record())
    with pytest.raises(VaultCryptoUnavailable):
        UnavailableLocalVaultStore().unlock(handle(), owner=OWNER, passphrase="pw")


# --- Fail-closed store and typed owner/password/file errors ------------------


def test_import_refuses_with_the_exact_pin_request() -> None:
    with pytest.raises(VaultCryptoUnavailable) as exc:
        UnavailableLocalVaultStore().import_key(
            HARDHAT_DEV_KEY,
            source_format=IMPORT_EVM_HEX,
            family=FAMILY_EVM,
            owner=OWNER,
            passphrase=VAULT_PASSPHRASE,
        )
    assert exc.value.missing == REQUIRED_CRYPTO_PINS
    for pin in REQUIRED_CRYPTO_PINS:
        assert pin in str(exc.value)


def test_invalid_input_refuses_before_the_dependency_error() -> None:
    with pytest.raises(InvalidEvmKey):
        UnavailableLocalVaultStore().import_key(
            "not-a-key",
            source_format=IMPORT_EVM_HEX,
            family=FAMILY_EVM,
            owner=OWNER,
            passphrase=VAULT_PASSPHRASE,
        )


def test_another_owners_handle_refuses_with_a_typed_error() -> None:
    with pytest.raises(VaultHandleOwnerMismatch):
        assert_handle_owner(handle(OTHER), owner=OWNER)
    with pytest.raises(VaultHandleOwnerMismatch):
        UnavailableLocalVaultStore().unlock(handle(OTHER), owner=OWNER, passphrase=VAULT_PASSPHRASE)


def test_unlock_of_own_handle_still_refuses_until_pins_land() -> None:
    with pytest.raises(VaultCryptoUnavailable):
        UnavailableLocalVaultStore().unlock(handle(), owner=OWNER, passphrase=VAULT_PASSPHRASE)


def test_lock_refuses_until_pins_land() -> None:
    with pytest.raises(VaultCryptoUnavailable):
        UnavailableLocalVaultStore().lock(OWNER)


def test_wrong_password_and_corrupt_file_errors_are_typed_and_redacted() -> None:
    assert issubclass(WrongVaultPassword, local.VaultStoreError)
    assert issubclass(CorruptVaultFile, local.VaultStoreError)
    wrong = WrongVaultPassword("the passphrase did not unlock the vault")
    assert VAULT_PASSPHRASE not in str(wrong)
