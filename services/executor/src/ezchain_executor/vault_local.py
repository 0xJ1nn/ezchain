"""The local-vault tier: import validation, a versioned file schema, typed refusals.

A key here is only ever **imported** — the platform does not create wallets, generate
seeds or produce a recovery kit. If the owner loses their passphrase or the encrypted
file, the key is lost; :data:`KEY_IMPORT_NOTICE` is the disclosure the product must show
at key-add (ADR-0040, ``DISCLOSURE_KEY_ADD``).

This module deliberately contains **no** AES, Argon2 or BIP39 code. The executor runtime
lock has none of ``cryptography``, ``argon2-cffi``, ``mnemonic`` or ``bip-utils``, and
home-made crypto is not an option for a key store. What is implemented here is the part
that needs no crypto: first-class format validation, derivation-path binding, the
versioned on-disk record shape, the authenticated-header bytes a cipher will need, and
a store seam that fails closed with a typed error until the reviewed pins land.

No value in this module can carry key material: :class:`ImportReceipt` holds only public
metadata and lengths, the file schema holds ciphertext, and every error message is a
fixed sentence. There is no operation that returns a key, a seed or a passphrase.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ezchain_core import KeyHandle

# The two key families and the three import formats, all first class.
FAMILY_SVM = "svm"
FAMILY_EVM = "evm"
KEY_FAMILIES: tuple[str, ...] = (FAMILY_SVM, FAMILY_EVM)

IMPORT_SOLANA_BASE58 = "solana_base58"
IMPORT_EVM_HEX = "evm_hex"
IMPORT_BIP39_MNEMONIC = "bip39_mnemonic"
IMPORT_FORMATS: tuple[str, ...] = (
    IMPORT_SOLANA_BASE58,
    IMPORT_EVM_HEX,
    IMPORT_BIP39_MNEMONIC,
)

# Per-chain BIP44 paths from the brief. A caller may omit the path and take the family's
# canonical one, or supply exactly the canonical path; anything else refuses.
SOLANA_DERIVATION_PATH = "m/44'/501'/0'/0'"
EVM_DERIVATION_PATH = "m/44'/60'/0'/0/0"
DERIVATION_PATHS: dict[str, str] = {
    FAMILY_SVM: SOLANA_DERIVATION_PATH,
    FAMILY_EVM: EVM_DERIVATION_PATH,
}
MNEMONIC_WORD_COUNTS: frozenset[int] = frozenset({12, 24})

# The file format from LIVE-EXECUTION.md §2: Argon2id for the KEK, AES-256-GCM for the
# per-key DEK, with the KDF parameters and nonce in the authenticated header.
VAULT_FILE_VERSION = 1
KDF_ALGORITHM = "argon2id"
CIPHER_ALGORITHM = "aes-256-gcm"
KDF_TIME_COST = 3
KDF_MEMORY_COST = 65536
KDF_PARALLELISM = 1
SALT_BYTES = 16
NONCE_BYTES = 12
SOLANA_SECRET_SIZES: tuple[int, ...] = (32, 64)
EVM_SECRET_SIZE = 32
MAX_BASE58_CHARS = 88
MAX_CIPHERTEXT_BYTES = 1024 * 1024

# Exact pins the field needs before any encryption can ship. They are a refusal detail,
# not a dependency declaration: the locks and the licence gate must be updated by the
# owner after review.
REQUIRED_CRYPTO_PINS: tuple[str, ...] = (
    "cryptography==50.0.2",
    "argon2-cffi==25.1.0",
    "mnemonic==0.21",
    "bip-utils==2.13.0",
)

KEY_IMPORT_NOTICE = (
    "ezCHAIN does not create, back up or recover your key. Import an existing key and "
    "keep your passphrase and encrypted vault file safe: losing either loses the key."
)

_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


# --- Typed refusals ----------------------------------------------------------


class VaultImportError(ValueError):
    """The import input is invalid. The offending value is never echoed."""


class UnsupportedImportFormat(VaultImportError):
    """The named import format is not one of the three first-class formats."""


class UnsupportedKeyFamily(VaultImportError):
    """The named key family is not supported, or does not match the import format."""


class InvalidSolanaKey(VaultImportError):
    """A Solana base58 secret key is malformed or is not 32 or 64 bytes."""


class InvalidEvmKey(VaultImportError):
    """An EVM hex private key is malformed or is not 32 bytes."""


class InvalidMnemonicShape(VaultImportError):
    """A mnemonic does not have 12 or 24 words.

    Wordlist and checksum validation require the ``mnemonic`` pin and are not performed
    here; this is shape validation only, never a claim that the sentence is valid BIP39.
    """


class DerivationPathMismatch(VaultImportError):
    """The derivation path differs from the canonical path for the chain family."""


class VaultStoreError(RuntimeError):
    """A local-vault store could not import, unlock or lock. Messages carry no secret."""


class WrongVaultPassword(VaultStoreError):
    """The passphrase did not unlock the vault."""


class CorruptVaultFile(VaultStoreError):
    """The vault file is malformed, tampered with or uses unsupported parameters."""


class UnsupportedVaultVersion(CorruptVaultFile):
    """The vault file's version is not one this build can read."""


class VaultHandleOwnerMismatch(VaultStoreError):
    """The handle belongs to another principal; it cannot be unlocked by this caller."""


class VaultCryptoUnavailable(VaultStoreError):
    """No reviewed crypto pin is installed, so no key may be imported or unlocked."""

    def __init__(self, missing: tuple[str, ...] = REQUIRED_CRYPTO_PINS) -> None:
        self.missing = missing
        super().__init__("encrypted local vault requires reviewed pins: " + ", ".join(missing))


# --- Import validation (no crypto) ------------------------------------------


@dataclass(frozen=True)
class ImportReceipt:
    """Public facts about a validated import. There is deliberately no key field.

    ``secret_bytes`` is a length, not material: 32 or 64 for a raw secret key, and
    ``None`` for a mnemonic whose derived key length cannot be known without BIP32.
    """

    family: str
    source_format: str
    derivation_path: str | None = None
    secret_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.family not in KEY_FAMILIES:
            raise UnsupportedKeyFamily("the key family is not supported")
        if self.source_format not in IMPORT_FORMATS:
            raise UnsupportedImportFormat("the import format is not supported")
        if self.secret_bytes is not None and self.secret_bytes <= 0:
            raise VaultImportError("a key length must be positive")


def _decode_base58(value: str) -> bytes:
    """Decode a base58 *encoding*. This is a codec, not a cryptographic primitive."""
    number = 0
    for char in value:
        digit = _BASE58_ALPHABET.find(char)
        if digit < 0:
            raise InvalidSolanaKey("a Solana secret key contains an invalid base58 character")
        number = number * 58 + digit
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading + raw


def parse_solana_base58(material: str) -> int:
    """Validate a Solana base58 secret key and return its decoded byte length."""
    text = material.strip()
    if not text or len(text) > MAX_BASE58_CHARS:
        raise InvalidSolanaKey("a Solana secret key has an unsupported encoded length")
    size = len(_decode_base58(text))
    if size not in SOLANA_SECRET_SIZES:
        raise InvalidSolanaKey("a Solana secret key must be 32 or 64 bytes")
    return size


def parse_evm_hex(material: str) -> int:
    """Validate an EVM hex private key and return its decoded byte length."""
    text = material.strip()
    if text.startswith(("0x", "0X")):
        text = text[2:]
    if len(text) != EVM_SECRET_SIZE * 2:
        raise InvalidEvmKey("an EVM private key must be 32 bytes of hex")
    try:
        size = len(bytes.fromhex(text))
    except ValueError:
        raise InvalidEvmKey("an EVM private key contains a non-hex character") from None
    if size != EVM_SECRET_SIZE:
        raise InvalidEvmKey("an EVM private key must be 32 bytes of hex")
    return size


def validate_mnemonic_shape(material: str) -> int:
    """Validate word count and token shape only; the checksum needs the ``mnemonic`` pin."""
    words = material.split()
    if len(words) not in MNEMONIC_WORD_COUNTS:
        raise InvalidMnemonicShape("a BIP39 mnemonic has 12 or 24 words")
    if any(not word.isalpha() for word in words):
        raise InvalidMnemonicShape("a BIP39 mnemonic contains a non-word token")
    return len(words)


def resolve_derivation_path(family: str, path: str | None = None) -> str:
    """Return the family's canonical path, or refuse a path that differs from it."""
    if family not in DERIVATION_PATHS:
        raise UnsupportedKeyFamily("the key family is not supported")
    expected = DERIVATION_PATHS[family]
    if path is None:
        return expected
    if path.strip() != expected:
        raise DerivationPathMismatch("the derivation path does not match the chain family")
    return expected


def validate_import(
    material: str,
    *,
    source_format: str,
    family: str,
    derivation_path: str | None = None,
) -> ImportReceipt:
    """Validate an import without retaining or returning any key material.

    ``material`` is only read. The optional mnemonic passphrase is a parameter of the
    encrypting store's ``import_key`` once the pins land; it has no field here and can
    never be printed by a receipt or an error.
    """
    if source_format not in IMPORT_FORMATS:
        raise UnsupportedImportFormat("the import format is not supported")
    path = resolve_derivation_path(family, derivation_path)

    if source_format == IMPORT_SOLANA_BASE58:
        if family != FAMILY_SVM:
            raise UnsupportedKeyFamily("a base58 secret key belongs to the svm family")
        size: int | None = parse_solana_base58(material)
    elif source_format == IMPORT_EVM_HEX:
        if family != FAMILY_EVM:
            raise UnsupportedKeyFamily("a hex private key belongs to the evm family")
        size = parse_evm_hex(material)
    else:
        validate_mnemonic_shape(material)
        size = None

    return ImportReceipt(
        family=family,
        source_format=source_format,
        derivation_path=path,
        secret_bytes=size,
    )


def assert_handle_owner(handle: KeyHandle, *, owner: str) -> None:
    """Refuse a handle that does not belong to the authenticated principal."""
    if not owner or handle.owner != owner:
        raise VaultHandleOwnerMismatch("the handle belongs to another owner")


# --- Versioned file schema (no crypto) --------------------------------------


@dataclass(frozen=True)
class KdfParams:
    """Argon2id parameters and salt as they appear in the authenticated header."""

    algorithm: str
    salt: bytes
    time_cost: int
    memory_cost: int
    parallelism: int

    def __post_init__(self) -> None:
        if self.algorithm != KDF_ALGORITHM:
            raise CorruptVaultFile("the vault file uses an unsupported KDF")
        if len(self.salt) != SALT_BYTES:
            raise CorruptVaultFile("the vault file has an invalid Argon2id salt")
        if not 1 <= self.time_cost <= 10:
            raise CorruptVaultFile("the vault file has invalid Argon2id parameters")
        if not 1024 <= self.memory_cost <= 1 << 20:
            raise CorruptVaultFile("the vault file has invalid Argon2id parameters")
        if not 1 <= self.parallelism <= 255:
            raise CorruptVaultFile("the vault file has invalid Argon2id parameters")


@dataclass(frozen=True)
class CipherParams:
    """AES-256-GCM nonce and ciphertext. The nonce is part of the AAD."""

    algorithm: str
    nonce: bytes
    ciphertext: bytes

    def __post_init__(self) -> None:
        if self.algorithm != CIPHER_ALGORITHM:
            raise CorruptVaultFile("the vault file uses an unsupported cipher")
        if len(self.nonce) != NONCE_BYTES:
            raise CorruptVaultFile("the vault file has an invalid GCM nonce")
        if not self.ciphertext or len(self.ciphertext) > MAX_CIPHERTEXT_BYTES:
            raise CorruptVaultFile("the vault file has an invalid ciphertext length")


@dataclass(frozen=True)
class VaultRecord:
    """One key's record in ``vault.json``, per LIVE-EXECUTION.md §2."""

    version: int
    id: str
    label: str
    family: str
    address: str
    kdf: KdfParams
    cipher: CipherParams
    created_at: str

    def __post_init__(self) -> None:
        if self.version != VAULT_FILE_VERSION:
            raise UnsupportedVaultVersion("the vault file version is not supported")
        if self.family not in KEY_FAMILIES:
            raise CorruptVaultFile("the vault file names an unsupported key family")
        if not self.id or not self.address or not self.created_at:
            raise CorruptVaultFile("the vault file is missing a required public field")


def _b64e(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64d(value: object) -> bytes:
    if not isinstance(value, str):
        raise CorruptVaultFile("the vault file has a non-string binary field")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        raise CorruptVaultFile("the vault file has an invalid binary field") from None


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def authenticated_header(record: VaultRecord) -> bytes:
    """The canonical AAD for AES-256-GCM: version and public/KDF header, never the ct."""
    return _canonical(
        {
            "version": record.version,
            "id": record.id,
            "family": record.family,
            "address": record.address,
            "kdf": {
                "alg": record.kdf.algorithm,
                "salt": _b64e(record.kdf.salt),
                "t": record.kdf.time_cost,
                "m": record.kdf.memory_cost,
                "p": record.kdf.parallelism,
            },
            "cipher": {"alg": record.cipher.algorithm, "nonce": _b64e(record.cipher.nonce)},
        }
    )


def encode_vault_file(records: tuple[VaultRecord, ...] | list[VaultRecord]) -> bytes:
    """Serialise the schema only. No encryption happens here."""
    payload = []
    for record in records:
        payload.append(
            {
                "version": record.version,
                "id": record.id,
                "label": record.label,
                "family": record.family,
                "address": record.address,
                "kdf": {
                    "alg": record.kdf.algorithm,
                    "salt": _b64e(record.kdf.salt),
                    "t": record.kdf.time_cost,
                    "m": record.kdf.memory_cost,
                    "p": record.kdf.parallelism,
                },
                "cipher": {
                    "alg": record.cipher.algorithm,
                    "nonce": _b64e(record.cipher.nonce),
                    "ct": _b64e(record.cipher.ciphertext),
                },
                "created_at": record.created_at,
            }
        )
    return _canonical({"records": payload})


def decode_vault_file(data: bytes) -> tuple[VaultRecord, ...]:
    """Parse the schema with strict fields and typed refusals; no decryption happens."""
    try:
        document = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CorruptVaultFile("the vault file is not valid JSON") from None
    if not isinstance(document, dict) or set(document) != {"records"}:
        raise CorruptVaultFile("the vault file has an invalid top-level shape")
    raw_records = document["records"]
    if not isinstance(raw_records, list):
        raise CorruptVaultFile("the vault file has an invalid record list")

    records: list[VaultRecord] = []
    for item in raw_records:
        records.append(_decode_record(item))
    return tuple(records)


def _decode_record(item: object) -> VaultRecord:
    if not isinstance(item, dict):
        raise CorruptVaultFile("the vault file has a non-object record")
    expected = {
        "version",
        "id",
        "label",
        "family",
        "address",
        "kdf",
        "cipher",
        "created_at",
    }
    if set(item) != expected:
        raise CorruptVaultFile("the vault file record has missing or extra fields")
    version = item["version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise CorruptVaultFile("the vault file record has an invalid version")
    if version != VAULT_FILE_VERSION:
        raise UnsupportedVaultVersion("the vault file version is not supported")

    kdf = item["kdf"]
    cipher = item["cipher"]
    if not isinstance(kdf, dict) or set(kdf) != {"alg", "salt", "t", "m", "p"}:
        raise CorruptVaultFile("the vault file has an invalid KDF header")
    if not isinstance(cipher, dict) or set(cipher) != {"alg", "nonce", "ct"}:
        raise CorruptVaultFile("the vault file has an invalid cipher header")
    for name in ("id", "label", "family", "address", "created_at"):
        if not isinstance(item[name], str):
            raise CorruptVaultFile("the vault file record has a non-string public field")
    for name in ("t", "m", "p"):
        if not isinstance(kdf[name], int) or isinstance(kdf[name], bool):
            raise CorruptVaultFile("the vault file has invalid Argon2id parameters")

    return VaultRecord(
        version=version,
        id=item["id"],
        label=item["label"],
        family=item["family"],
        address=item["address"],
        kdf=KdfParams(
            algorithm=kdf["alg"],
            salt=_b64d(kdf["salt"]),
            time_cost=kdf["t"],
            memory_cost=kdf["m"],
            parallelism=kdf["p"],
        ),
        cipher=CipherParams(
            algorithm=cipher["alg"],
            nonce=_b64d(cipher["nonce"]),
            ciphertext=_b64d(cipher["ct"]),
        ),
        created_at=item["created_at"],
    )


# --- The fail-closed store seam ---------------------------------------------


@runtime_checkable
class LocalVaultStore(Protocol):
    """The local-vault tier's operations. Unlock returns a handle, never a key.

    ``import_key`` takes the material as a plain argument so no long-lived object can
    hold it; the implementation must validate it before doing any crypto and must never
    include it in an error. ``passphrase`` is the owner's vault credential, not a BIP39
    passphrase; the mnemonic passphrase, when present, belongs to the import material's
    derivation step and is likewise never stored in a receipt.
    """

    def import_key(
        self,
        material: str,
        *,
        source_format: str,
        family: str,
        owner: str,
        passphrase: str,
        derivation_path: str | None = None,
    ) -> KeyHandle: ...

    def unlock(self, handle: KeyHandle, *, owner: str, passphrase: str) -> KeyHandle: ...

    def lock(self, owner: str) -> None: ...


@dataclass(frozen=True)
class UnavailableLocalVaultStore:
    """A store that refuses every operation until the crypto pins are reviewed.

    Validation and owner binding still run, so a malformed key or a cross-user handle
    gets its precise typed error even though encryption is blocked. Nothing is written
    to disk and no passphrase is ever inspected.
    """

    missing: tuple[str, ...] = REQUIRED_CRYPTO_PINS

    def import_key(
        self,
        material: str,
        *,
        source_format: str,
        family: str,
        owner: str,
        passphrase: str,
        derivation_path: str | None = None,
    ) -> KeyHandle:
        validate_import(
            material,
            source_format=source_format,
            family=family,
            derivation_path=derivation_path,
        )
        raise VaultCryptoUnavailable(self.missing)

    def unlock(self, handle: KeyHandle, *, owner: str, passphrase: str) -> KeyHandle:
        assert_handle_owner(handle, owner=owner)
        raise VaultCryptoUnavailable(self.missing)

    def lock(self, owner: str) -> None:
        raise VaultCryptoUnavailable(self.missing)


__all__ = [
    "CIPHER_ALGORITHM",
    "DERIVATION_PATHS",
    "EVM_DERIVATION_PATH",
    "FAMILY_EVM",
    "FAMILY_SVM",
    "IMPORT_BIP39_MNEMONIC",
    "IMPORT_EVM_HEX",
    "IMPORT_FORMATS",
    "IMPORT_SOLANA_BASE58",
    "KDF_ALGORITHM",
    "KEY_FAMILIES",
    "KEY_IMPORT_NOTICE",
    "MNEMONIC_WORD_COUNTS",
    "REQUIRED_CRYPTO_PINS",
    "SOLANA_DERIVATION_PATH",
    "VAULT_FILE_VERSION",
    "CorruptVaultFile",
    "DerivationPathMismatch",
    "ImportReceipt",
    "InvalidEvmKey",
    "InvalidMnemonicShape",
    "InvalidSolanaKey",
    "KdfParams",
    "CipherParams",
    "LocalVaultStore",
    "UnsupportedImportFormat",
    "UnsupportedKeyFamily",
    "UnsupportedVaultVersion",
    "UnavailableLocalVaultStore",
    "VaultCryptoUnavailable",
    "VaultHandleOwnerMismatch",
    "VaultImportError",
    "VaultRecord",
    "VaultStoreError",
    "WrongVaultPassword",
    "assert_handle_owner",
    "authenticated_header",
    "decode_vault_file",
    "encode_vault_file",
    "parse_evm_hex",
    "parse_solana_base58",
    "resolve_derivation_path",
    "validate_import",
    "validate_mnemonic_shape",
]
