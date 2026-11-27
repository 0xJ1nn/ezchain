"""Real public byte fixtures: provenance and wire-layer evidence.

Every family has at least one fixture captured from an authoritative public source. A
fixture records its source URL, retrieval date, SHA-256 and why it holds no secret; the
bytes are checked against their hash on every run. These fixtures prove the *documented
wire layer* only. The constructed negative vectors in the other chain tests stay clearly
labelled as constructed and are not presented as captured chain evidence.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "chains"
FAMILIES = ("solana", "evm", "ton", "sui", "cosmos")
REQUIRED = ("family", "source_url", "retrieved_at", "sha256", "why_no_secret", "encoding", "bytes")


def load() -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8")) for path in sorted(FIXTURES.glob("*.json"))
    ]


def raw(item: dict[str, Any]) -> bytes:
    return base64.b64decode(item["bytes"], validate=True)


@pytest.mark.parametrize("family", FAMILIES)
def test_each_family_has_a_real_public_fixture(family: str) -> None:
    assert any(item["family"] == family for item in load())


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")))
def test_fixture_provenance_is_complete_and_hashes(path: Path) -> None:
    item = json.loads(path.read_text(encoding="utf-8"))
    for key in REQUIRED:
        assert item.get(key), f"{path.name} has no {key}"
    assert item["family"] in FAMILIES
    assert item["source_url"].startswith("https://")
    assert item["retrieved_at"] == "2026-11-27"
    assert item["encoding"] == "base64"
    assert len(item["sha256"]) == 64
    assert hashlib.sha256(raw(item)).hexdigest() == item["sha256"]
    assert item["why_no_secret"].strip()
    assert "constructed" not in item["why_no_secret"].lower()


def test_no_fixture_carries_a_key_or_a_signature_secret_field() -> None:
    for item in load():
        text = json.dumps(item).lower()
        for banned in ("private_key", "seed_phrase", "mnemonic", "passphrase"):
            assert banned not in text


def fixture_for(family: str) -> dict[str, Any]:
    return next(item for item in load() if item["family"] == family)


def test_solana_fixture_is_a_real_signed_legacy_message() -> None:
    from ezchain_adapters.chains.solana import _read_compact_u16

    raw = raw_bytes_for("solana")
    signatures, offset = _read_compact_u16(raw, 0)
    assert signatures == 1
    assert len(raw) - offset > 64 + 3 + 32  # signature, message header, blockhash
    assert raw[offset + 64] == 1  # num_required_signatures of the legacy message


def raw_bytes_for(family: str) -> bytes:
    return raw(fixture_for(family))


def test_evm_fixture_is_a_real_eip155_legacy_transaction() -> None:
    from ezchain_adapters.chains.evm import rlp_decode

    tx = rlp_decode(raw_bytes_for("evm"))
    assert isinstance(tx, list) and len(tx) == 9
    assert isinstance(tx[3], bytes) and len(tx[3]) == 20
    assert int.from_bytes(tx[6], "big") == 27  # v, EIP-155 legacy
    assert isinstance(tx[7], bytes) and len(tx[7]) == 32


def test_ton_fixture_is_a_real_single_cell_boc() -> None:
    data = raw_bytes_for("ton")
    assert data.startswith(bytes.fromhex("b5ee9c72"))
    assert data[4] == 0x41  # one-byte refs index plus the corpus CRC32 flag
    assert int.from_bytes(data[5:6], "big") == 1  # one cell
    assert data[13:17] == bytes.fromhex("0000002a")  # published uint32 42 vector


def test_sui_fixture_is_official_bcs_transaction_data() -> None:
    data = raw_bytes_for("sui")
    # Sui signs blake2b(intent || bcs(TransactionData)); the SDK test publishes this digest.
    digest = hashlib.blake2b(b"\x00\x00\x00" + data, digest_size=32).digest()
    assert (
        base64.b64encode(digest).decode("ascii") == "VMVv+/L/EG7/yhEbCQ1qiSt30JXV8yIm+4xO6yTkqeM="
    )


def test_cosmos_fixture_hash_matches_the_documented_txhash() -> None:
    data = raw_bytes_for("cosmos")
    # CometBFT derives the txhash as sha256(serialize(TxRaw)); this byte-for-byte identity
    # is the evidence. Our strict TxBody subset decoder is not claimed to parse arbitrary
    # signed TxRaw messages — those carry auth info and signatures as well.
    assert hashlib.sha256(data).hexdigest() == (
        "d70952032620cc4e2737eb8ac379806359d8e0b17b0488f627997a0b043abded"
    )
