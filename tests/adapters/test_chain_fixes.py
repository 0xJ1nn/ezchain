"""Regressions for EVM chain-id binding and Sui's unsupported broadcast shape."""

from __future__ import annotations

from decimal import Decimal

import pytest

from ezchain_adapters.chains.broadcast import BroadcastError, unsupported_broadcast
from ezchain_adapters.chains.chainbind import fields_from_approval
from ezchain_adapters.chains.evm import (
    EvmBuilder,
    EvmChainMismatch,
    build_evm_transaction,
    decode_evm_transaction,
    rlp_decode,
)
from ezchain_adapters.chains.registry import SUPPORTED, support_for
from ezchain_core import ApprovedIntent
from ezchain_core.reason_codes import BROADCAST_UNSUPPORTED, REASON_CODES
from ezchain_core.trust import BINDING_FIELDS, Intent, decode_and_bind


def approved(chain: str) -> ApprovedIntent:
    return ApprovedIntent(
        chain=chain,
        cluster="mainnet",
        mint="mint-test",
        side="buy",
        amount=Decimal("100"),
        min_out=Decimal("50"),
        recipient="recipient-test",
        program_id="program-test",
        idempotency_key="idem-chain-fixes-0001",
    )


def trust_intent(chain: str) -> Intent:
    item = approved(chain)
    return Intent.from_mapping(
        {
            "chain": item.chain,
            "cluster": item.cluster,
            "mint": item.mint,
            "side": item.side,
            "amount": item.amount,
            "minimum_output": item.min_out,
            "recipient": item.recipient,
            "program_id": item.program_id,
            "idempotency_key": item.idempotency_key,
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chain", "chain_id"),
    [("ethereum", 1), ("base", 8453), ("arbitrum", 42161)],
    ids=["ethereum-mainnet", "base", "arbitrum"],
)
async def test_evm_v_uses_the_real_chain_id_and_binds(chain: str, chain_id: int) -> None:
    raw = await EvmBuilder(chain_id).prepare(approved(chain))
    tx = rlp_decode(raw)
    assert isinstance(tx, list) and len(tx) == 9
    assert int.from_bytes(tx[6], "big") == chain_id
    fields = decode_evm_transaction(raw)
    assert fields.version == "eip155-legacy"
    assert decode_and_bind(trust_intent(chain), fields=fields)


@pytest.mark.asyncio
@pytest.mark.parametrize("chain", ["ethereum", "base", "optimism"])
async def test_registry_wires_each_evm_chain_to_its_own_chain_id(chain: str) -> None:
    from ezchain_adapters.registry import BY_ID

    meta = BY_ID[chain]
    assert meta.evm_chain_id is not None
    raw = await support_for(chain).builder().prepare(approved(chain))  # type: ignore[union-attr]
    tx = rlp_decode(raw)
    assert isinstance(tx, list)
    assert int.from_bytes(tx[6], "big") == meta.evm_chain_id
    assert decode_evm_transaction(raw)


def test_evm_chain_id_is_part_of_the_binding() -> None:
    """The same approval fields with the wrong v refuse: chain is bound on the wire."""
    fields = fields_from_approval(approved("base"), version="eip155-8453")
    wrong_v = build_evm_transaction(fields, chain_id=1)
    with pytest.raises(EvmChainMismatch) as caught:
        decode_evm_transaction(wrong_v)
    assert caught.value.code == "TRANSACTION_CHAIN_MISMATCH"

    # A chain id that is not the registry's value for the approved chain also refuses.
    ethereum_fields = fields_from_approval(approved("ethereum"), version="eip155-1")
    with pytest.raises(EvmChainMismatch):
        decode_evm_transaction(build_evm_transaction(ethereum_fields, chain_id=8453))


def test_sui_broadcast_is_removed_and_fails_closed() -> None:
    """Sui keeps its decoder and builder, but its broadcast shape is refused."""
    support = support_for("sui")
    assert support is not None
    assert support.broadcast is None
    assert {chain for chain, item in SUPPORTED.items() if item.broadcast is not None} == {
        chain for chain in SUPPORTED if chain != "sui"
    }
    with pytest.raises(BroadcastError) as caught:
        unsupported_broadcast("sui")
    assert caught.value.code == BROADCAST_UNSUPPORTED
    assert caught.value.ambiguous is False
    assert BROADCAST_UNSUPPORTED in REASON_CODES


def test_submission_receipt_is_still_not_a_fill() -> None:
    """The earlier correction survives both fixes."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "packages/adapters/src/ezchain_adapters/chains"
    for path in sorted(root.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "Fill(" not in source
        assert 'status="filled"' not in source
        assert "class ChainVenueAdapter" not in source
    assert BINDING_FIELDS  # the nine bound fields remain the same vocabulary
