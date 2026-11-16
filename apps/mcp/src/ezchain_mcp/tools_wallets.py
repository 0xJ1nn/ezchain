"""Wallet tools: follow an address and read what it did.

Every tool declares whether it uses a free or a paid source and what it does, so
an agent can tell a read from a configuration change before calling it. The paid
explorer source is resolved from the person's own key at the API edge; the agent
never sees a credential.
"""

from __future__ import annotations

from typing import Any

from .client import EzchainClient
from .tools import Tool, _object, _seg


def _list_wallets(client: EzchainClient, _: dict[str, Any]) -> Any:
    return client.get("/api/v1/wallets/tracked")


def _get_wallet(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.get(f"/api/v1/wallets/tracked/{_seg(args['wallet_id'])}")


def _get_activity(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.get(f"/api/v1/wallets/tracked/{_seg(args['wallet_id'])}/activity")


def _track_wallet(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.post(
        "/api/v1/wallets/track",
        json={key: args[key] for key in ("chain", "address", "label")},
    )


def _untrack_wallet(client: EzchainClient, args: dict[str, Any]) -> Any:
    client.delete(f"/api/v1/wallets/tracked/{_seg(args['wallet_id'])}")
    return {"deleted": args["wallet_id"]}


def _refresh_wallet(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.post(f"/api/v1/wallets/tracked/{_seg(args['wallet_id'])}/refresh")


TOOLS: tuple[Tool, ...] = (
    Tool(
        "ezchain_list_tracked_wallets",
        "List tracked wallets",
        "Wallets the person follows, with their chain, address and label.",
        _object({}),
        _list_wallets,
        cost="free",
        effect="read",
    ),
    Tool(
        "ezchain_get_wallet",
        "Get tracked wallet",
        "One followed wallet: holdings, 30-day realized P&L and win rate. Unknown stays unknown.",
        _object({"wallet_id": {"type": "string"}}, ["wallet_id"]),
        _get_wallet,
        cost="free",
        effect="read",
    ),
    Tool(
        "ezchain_get_wallet_activity",
        "Get wallet activity",
        "Recent observed swaps for one followed wallet, newest first.",
        _object({"wallet_id": {"type": "string"}}, ["wallet_id"]),
        _get_activity,
        cost="free",
        effect="read",
    ),
    Tool(
        "ezchain_track_wallet",
        "Track wallet",
        "Follow an address on a supported chain with a label. Identical repeats are idempotent.",
        _object(
            {
                "chain": {"type": "string"},
                "address": {"type": "string"},
                "label": {"type": "string"},
            },
            ["chain", "address", "label"],
        ),
        _track_wallet,
        read_only=False,
        cost="free",
        effect="configure",
    ),
    Tool(
        "ezchain_untrack_wallet",
        "Untrack wallet",
        "Stop following a wallet. Its recorded observations are kept as evidence.",
        _object({"wallet_id": {"type": "string"}}, ["wallet_id"]),
        _untrack_wallet,
        read_only=False,
        cost="free",
        effect="configure",
    ),
    Tool(
        "ezchain_refresh_wallet",
        "Refresh wallet activity",
        "Pull recent swaps from the wallet's sources. A repeat records nothing new.",
        _object({"wallet_id": {"type": "string"}}, ["wallet_id"]),
        _refresh_wallet,
        read_only=False,
        cost="free",
        effect="configure",
    ),
)

READ_TOOLS: tuple[Tool, ...] = tuple(tool for tool in TOOLS if tool.read_only)
WRITE_TOOLS: tuple[Tool, ...] = tuple(tool for tool in TOOLS if not tool.read_only)

__all__ = ["READ_TOOLS", "TOOLS", "WRITE_TOOLS"]
