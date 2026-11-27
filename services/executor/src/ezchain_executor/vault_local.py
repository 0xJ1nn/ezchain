"""The local-vault tier: import validation, a versioned file schema, encryption at rest.

A key here is only ever **imported** — the platform does not create wallets, generate
seeds or produce a recovery kit. If the owner loses their passphrase or the encrypted
file, the key is lost; :data:`KEY_IMPORT_NOTICE` is the disclosure the product must show
at key-add (ADR-0040, ``DISCLOSURE_KEY_ADD``).

Cryptography comes from the reviewed libraries: ``cryptography`` for AES-256-GCM,
``argon2-cffi`` for the Argon2id KEK and ``mnemonic`` for BIP39 wordlist/checksum
validation and the optional-passphrase seed. Derivation paths are first-class public
binding, not a child-key derivation: no chain SDK signs and no second boundary is added.
The three imports are lazy, so a checkout without the pins still refuses with a typed
:class:`VaultCryptoUnavailable` rather than failing at import time.

No public value carries key material: :class:`ImportReceipt` holds only public metadata
and lengths, the file schema holds ciphertext, every error is a fixed sentence, and the
encrypted store returns a :class:`~ezchain_core.vault.KeyHandle`. The private open helper
authenticates a record during unlock and discards the bytes it verifies.
"""

from __future__ import annotations

import base64
import binascii
import json
import secrets
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from ezchain_core import CUSTODY_LOCAL_VAULT, Custody, KeyHandle, is_fingerprint

if TYPE_CHECKING:
    from argon2.low_level import Type
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

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
# The chain label a handle carries. Public metadata only: no address is derived here.
CHAIN_BY_FAMILY: dict[str, str] = {FAMILY_SVM: "solana", FAMILY_EVM: "evm"}
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
# A whole file, not one record: a few megabytes leaves room for several maximum-sized
# records and many ordinary ones, while bounding what an unauthenticated file can make
# the process read and parse.
MAX_VAULT_FILE_BYTES = 8 * 1024 * 1024

# Exact pins the encrypted tier needs. They are a refusal detail, not a declaration:
# the range files and locks are the install source, and these names match those pins.
# ``bip-utils`` is deliberately absent: its resolved set pulls signer/custody libraries
# that the repository's pin tripwire forbids without an ADR, and no child key is derived
# here. BIP39 validation and the optional passphrase come from ``mnemonic`` instead.
REQUIRED_CRYPTO_PINS: tuple[str, ...] = (
    "cryptography==50.0.1",
    "argon2-cffi==25.1.0",
    "mnemonic==0.21",
)

# Fixed sentences: a refusal never echoes a passphrase, a key or any ciphertext bytes.
WRONG_PASSWORD_MESSAGE = "the passphrase did not unlock the vault"
CORRUPTED_VAULT_MESSAGE = "the vault file has invalid encryption parameters"
EMPTY_PASSPHRASE_MESSAGE = "a local vault key needs a non-empty passphrase"
HANDLE_BINDING_MESSAGE = "the handle does not match the vault record"
VAULT_FILE_TOO_LARGE_MESSAGE = "the vault file is too large"
UNSUPPORTED_KDF_PARAMETERS_MESSAGE = "the vault file uses unsupported Argon2id parameters"

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
    """The passphrase, the AAD or the ciphertext did not authenticate.

    A wrong credential and a tampered file are deliberately indistinguishable to a
    caller: one fixed sentence covers both.
    """

    def __init__(self, message: str = WRONG_PASSWORD_MESSAGE) -> None:
        super().__init__(message)


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


def _decode_evm_hex(material: str) -> bytes:
    """Decode a validated EVM hex secret. This is a codec, not a cryptographic primitive."""
    text = material.strip()
    if text.startswith(("0x", "0X")):
        text = text[2:]
    if len(text) != EVM_SECRET_SIZE * 2:
        raise InvalidEvmKey("an EVM private key must be 32 bytes of hex")
    try:
        raw = bytes.fromhex(text)
    except ValueError:
        raise InvalidEvmKey("an EVM private key contains a non-hex character") from None
    if len(raw) != EVM_SECRET_SIZE:
        raise InvalidEvmKey("an EVM private key must be 32 bytes of hex")
    return raw


def parse_evm_hex(material: str) -> int:
    """Validate an EVM hex private key and return its decoded byte length."""
    return len(_decode_evm_hex(material))


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
        # Version 1 accepts only the reviewed parameters this build writes. An
        # unauthenticated header must never choose a cost the KDF would spend before
        # AES-GCM gets a chance to reject the file.
        if (self.time_cost, self.memory_cost, self.parallelism) != (
            KDF_TIME_COST,
            KDF_MEMORY_COST,
            KDF_PARALLELISM,
        ):
            raise CorruptVaultFile(UNSUPPORTED_KDF_PARAMETERS_MESSAGE)


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
    fingerprint: str
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
        if not is_fingerprint(self.fingerprint):
            raise CorruptVaultFile("the vault file has an invalid public fingerprint")


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


def _header(
    version: int,
    record_id: str,
    family: str,
    address: str,
    fingerprint: str,
    kdf: KdfParams,
    nonce: bytes,
) -> bytes:
    """The canonical AAD: version and public/KDF header, never the ciphertext."""
    return _canonical(
        {
            "version": version,
            "id": record_id,
            "family": family,
            "address": address,
            "fingerprint": fingerprint,
            "kdf": {
                "alg": kdf.algorithm,
                "salt": _b64e(kdf.salt),
                "t": kdf.time_cost,
                "m": kdf.memory_cost,
                "p": kdf.parallelism,
            },
            "cipher": {"alg": CIPHER_ALGORITHM, "nonce": _b64e(nonce)},
        }
    )


def authenticated_header(record: VaultRecord) -> bytes:
    """The canonical AAD for AES-256-GCM: version and public/KDF header, never the ct."""
    return _header(
        record.version,
        record.id,
        record.family,
        record.address,
        record.fingerprint,
        record.kdf,
        record.cipher.nonce,
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
                "fingerprint": record.fingerprint,
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
        "fingerprint",
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
    for name in ("id", "label", "family", "address", "fingerprint", "created_at"):
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
        fingerprint=item["fingerprint"],
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


# --- Reviewed crypto, loaded lazily ------------------------------------------


def _load_crypto() -> tuple[Callable[..., bytes], type[Type], type[AESGCM], type[InvalidTag]]:
    """Import the reviewed primitives on demand, or fail closed with a typed refusal."""
    try:
        from argon2.low_level import Type, hash_secret_raw
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise VaultCryptoUnavailable() from None
    return hash_secret_raw, Type, AESGCM, InvalidTag


def _derive_kek(passphrase: str, kdf: KdfParams) -> bytes:
    """Derive a 32-byte KEK from the vault credential with the record's Argon2id params."""
    hash_secret_raw, Type, _, _ = _load_crypto()
    try:
        return hash_secret_raw(
            passphrase.encode("utf-8"),
            salt=kdf.salt,
            time_cost=kdf.time_cost,
            memory_cost=kdf.memory_cost,
            parallelism=kdf.parallelism,
            hash_len=32,
            type=Type.ID,
        )
    except Exception:
        raise CorruptVaultFile(CORRUPTED_VAULT_MESSAGE) from None


def _seal_secret(
    *,
    secret: bytes,
    passphrase: str,
    kdf: KdfParams,
    nonce: bytes,
    header: bytes,
) -> bytes:
    """Encrypt one secret under the KEK, binding the header as AEAD associated data."""
    _, _, AESGCM, _ = _load_crypto()
    return AESGCM(_derive_kek(passphrase, kdf)).encrypt(nonce, secret, header)


def _open_secret(record: VaultRecord, passphrase: str) -> bytes:
    """Authenticate and open one record. Internal: the store discards these bytes."""
    _, _, AESGCM, InvalidTag = _load_crypto()
    try:
        return AESGCM(_derive_kek(passphrase, record.kdf)).decrypt(
            record.cipher.nonce, record.cipher.ciphertext, authenticated_header(record)
        )
    except InvalidTag:
        raise WrongVaultPassword() from None
    except Exception:
        raise CorruptVaultFile(CORRUPTED_VAULT_MESSAGE) from None


def _bip39_seed(material: str, mnemonic_passphrase: str | None) -> bytes:
    """Validate a BIP39 sentence with the reviewed library and derive its seed."""
    try:
        from mnemonic import Mnemonic
    except ImportError:
        raise VaultCryptoUnavailable() from None
    words = material.split()
    if len(words) not in MNEMONIC_WORD_COUNTS:
        raise InvalidMnemonicShape("a BIP39 mnemonic has 12 or 24 words")
    bip39 = Mnemonic("english")
    try:
        valid = bip39.check(material)
    except (ValueError, KeyError):
        raise InvalidMnemonicShape("the BIP39 mnemonic failed wordlist validation") from None
    if not valid:
        raise InvalidMnemonicShape("the BIP39 mnemonic failed checksum validation")
    try:
        return bip39.to_seed(material, passphrase=mnemonic_passphrase or "")
    except Exception:
        raise VaultImportError("the BIP39 mnemonic could not be turned into a seed") from None


def _secret_from_import(
    material: str, source_format: str, mnemonic_passphrase: str | None
) -> bytes:
    """Turn already-validated import material into the bytes the vault seals."""
    if source_format == IMPORT_SOLANA_BASE58:
        return _decode_base58(material.strip())
    if source_format == IMPORT_EVM_HEX:
        return _decode_evm_hex(material)
    return _bip39_seed(material, mnemonic_passphrase)


# --- The fail-closed store seam ---------------------------------------------


@runtime_checkable
class LocalVaultStore(Protocol):
    """The local-vault tier's operations. Unlock returns a handle, never a key.

    ``import_key`` takes the material as a plain argument so no long-lived object can
    hold it; the implementation must validate it before doing any crypto and must never
    include it in an error. ``passphrase`` is the owner's vault credential, not a BIP39
    passphrase; ``mnemonic_passphrase``, when present, is used only to derive the BIP39
    seed and is never stored in a receipt or a file. ``address`` and ``fingerprint`` are
    the public facts the caller already owns: this tier derives neither, so no chain
    signing library enters the vault.
    """

    def import_key(
        self,
        material: str,
        *,
        source_format: str,
        family: str,
        owner: str,
        passphrase: str,
        address: str = "",
        fingerprint: str = "",
        derivation_path: str | None = None,
        mnemonic_passphrase: str | None = None,
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
        address: str = "",
        fingerprint: str = "",
        derivation_path: str | None = None,
        mnemonic_passphrase: str | None = None,
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


@dataclass(frozen=True)
class EncryptedLocalVaultStore:
    """The local vault: one versioned file, one sealed record per imported key.

    The file is rewritten atomically with mode 0600. Only ciphertext, public metadata
    and KDF parameters are ever persisted; the private ``_unlocked`` index holds opaque
    handle ids, never the bytes that were decrypted to authenticate an unlock.
    """

    path: Path
    _unlocked: dict[str, set[str]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def _read_records(self) -> tuple[VaultRecord, ...]:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return ()
        if size > MAX_VAULT_FILE_BYTES:
            raise CorruptVaultFile(VAULT_FILE_TOO_LARGE_MESSAGE)
        return decode_vault_file(self.path.read_bytes())

    def _write_records(self, records: tuple[VaultRecord, ...]) -> None:
        """Replace the file in one step so a crash cannot leave a half-written record."""
        payload = encode_vault_file(records)
        if len(payload) > MAX_VAULT_FILE_BYTES:
            raise CorruptVaultFile(VAULT_FILE_TOO_LARGE_MESSAGE)
        temporary = ""
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.path.parent, prefix=".vault-", delete=False
            ) as handle:
                temporary = handle.name
                handle.write(payload)
            Path(temporary).chmod(0o600)
            Path(temporary).replace(self.path)
        except BaseException:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
            raise

    def import_key(
        self,
        material: str,
        *,
        source_format: str,
        family: str,
        owner: str,
        passphrase: str,
        address: str = "",
        fingerprint: str = "",
        derivation_path: str | None = None,
        mnemonic_passphrase: str | None = None,
    ) -> KeyHandle:
        """Validate, seal and persist an imported secret; return a public-only handle."""
        validate_import(
            material,
            source_format=source_format,
            family=family,
            derivation_path=derivation_path,
        )
        if not address:
            raise VaultImportError("a local vault key needs its public address")
        if not is_fingerprint(fingerprint):
            raise VaultImportError("a local vault key needs a public-key fingerprint")
        if not passphrase:
            raise VaultImportError(EMPTY_PASSPHRASE_MESSAGE)
        _load_crypto()

        secret = _secret_from_import(material, source_format, mnemonic_passphrase)
        salt = secrets.token_bytes(SALT_BYTES)
        nonce = secrets.token_bytes(NONCE_BYTES)
        kdf = KdfParams(
            algorithm=KDF_ALGORITHM,
            salt=salt,
            time_cost=KDF_TIME_COST,
            memory_cost=KDF_MEMORY_COST,
            parallelism=KDF_PARALLELISM,
        )
        record_id = "h-" + secrets.token_hex(16)
        ciphertext = _seal_secret(
            secret=secret,
            passphrase=passphrase,
            kdf=kdf,
            nonce=nonce,
            header=_header(VAULT_FILE_VERSION, record_id, family, address, fingerprint, kdf, nonce),
        )
        record = VaultRecord(
            version=VAULT_FILE_VERSION,
            id=record_id,
            label=f"{family} imported key",
            family=family,
            address=address,
            fingerprint=fingerprint,
            kdf=kdf,
            cipher=CipherParams(algorithm=CIPHER_ALGORITHM, nonce=nonce, ciphertext=ciphertext),
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        records = tuple(item for item in self._read_records() if item.id != record_id)
        self._write_records((*records, record))
        return KeyHandle(
            handle_id=record_id,
            owner=owner,
            custody=cast(Custody, CUSTODY_LOCAL_VAULT),
            chain=CHAIN_BY_FAMILY[family],
            address=address,
            fingerprint=fingerprint,
        )

    def unlock(self, handle: KeyHandle, *, owner: str, passphrase: str) -> KeyHandle:
        """Authenticate the record behind an owned handle; the verified bytes are dropped."""
        assert_handle_owner(handle, owner=owner)
        if handle.custody != CUSTODY_LOCAL_VAULT:
            raise CorruptVaultFile(HANDLE_BINDING_MESSAGE)
        matches = [item for item in self._read_records() if item.id == handle.handle_id]
        if len(matches) != 1:
            raise CorruptVaultFile("the vault file has no record for this handle")
        record = matches[0]
        if (
            record.address != handle.address
            or record.fingerprint != handle.fingerprint
            or CHAIN_BY_FAMILY.get(record.family) != handle.chain
        ):
            raise CorruptVaultFile(HANDLE_BINDING_MESSAGE)
        _open_secret(record, passphrase)
        self._unlocked.setdefault(owner, set()).add(handle.handle_id)
        return handle

    def lock(self, owner: str) -> None:
        """Forget this principal's unlocked handles; no key bytes were ever retained."""
        self._unlocked.pop(owner, None)

    def unlocked_handles(self, owner: str) -> tuple[str, ...]:
        """Opaque ids currently unlocked for a principal, safe to log."""
        return tuple(sorted(self._unlocked.get(owner, ())))


__all__ = [
    "CIPHER_ALGORITHM",
    "CORRUPTED_VAULT_MESSAGE",
    "DERIVATION_PATHS",
    "EMPTY_PASSPHRASE_MESSAGE",
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
    "HANDLE_BINDING_MESSAGE",
    "MAX_VAULT_FILE_BYTES",
    "MNEMONIC_WORD_COUNTS",
    "REQUIRED_CRYPTO_PINS",
    "SOLANA_DERIVATION_PATH",
    "UNSUPPORTED_KDF_PARAMETERS_MESSAGE",
    "VAULT_FILE_TOO_LARGE_MESSAGE",
    "VAULT_FILE_VERSION",
    "WRONG_PASSWORD_MESSAGE",
    "CHAIN_BY_FAMILY",
    "CorruptVaultFile",
    "DerivationPathMismatch",
    "ImportReceipt",
    "InvalidEvmKey",
    "InvalidMnemonicShape",
    "InvalidSolanaKey",
    "KdfParams",
    "CipherParams",
    "EncryptedLocalVaultStore",
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
