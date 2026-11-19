"""Track any wallet, read what it did, and see its performance honestly."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ezchain_adapters.wallet_sources import (
    EvmExplorerSource,
    FirehoseLaunchBuyersSource,
    SolanaPublicRpcSource,
    WalletActivitySource,
)

from ..config import Settings, get_settings
from ..deps import get_secret_cipher, get_session
from ..services import provider_keys
from ..services import wallet_activity as wallets
from ..services.provider_secrets import SecretCipher

router = APIRouter(prefix="/api/v1/wallets", tags=["wallets"])


class WalletTrackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain: str
    address: str
    label: str


class WalletOut(BaseModel):
    id: str
    chain: str
    address: str
    label: str
    created_at: str


class SwapOut(BaseModel):
    source_tx: str
    source: str
    chain: str
    side: str
    token_address: str
    token_symbol: str | None
    amount_tokens: str | None
    amount_usd: str | None
    observed_at: str


class WalletDetailOut(WalletOut):
    holdings: dict[str, str | None]
    realized_pnl_30d_usd: str | None
    win_rate_30d: str | None
    recent_swaps: list[SwapOut]
    mode: str


class RefreshOut(BaseModel):
    inserted: int


def get_wallet_sources(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    cipher: SecretCipher = Depends(get_secret_cipher),
) -> list[WalletActivitySource]:
    """Free sources first; the paid explorer source only with the person's own key."""
    sources: list[WalletActivitySource] = [SolanaPublicRpcSource(), FirehoseLaunchBuyersSource()]
    try:
        key = provider_keys.resolve_secret(
            session,
            workspace_id=settings.workspace_id,
            provider="etherscan",
            cipher=cipher,
        )
        sources.append(EvmExplorerSource(key))
    except Exception:  # noqa: BLE001 - no key means no paid source, not a broken endpoint
        pass
    return sources


def _wallet_error(exc: wallets.WalletError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


@router.post("/track", response_model=WalletOut, status_code=status.HTTP_201_CREATED)
def track(
    payload: WalletTrackIn, response: Response, session: Session = Depends(get_session)
) -> Any:
    try:
        row, created = wallets.track(
            session, chain=payload.chain, address=payload.address, label=payload.label
        )
    except wallets.WalletError as exc:
        raise _wallet_error(exc) from exc
    if not created:
        response.status_code = status.HTTP_200_OK
    return wallets.wallet_out(row)


@router.get("/tracked", response_model=list[WalletOut])
def list_wallets(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [wallets.wallet_out(row) for row in wallets.list_tracked(session)]


@router.get("/tracked/{wallet_id}", response_model=WalletDetailOut)
def detail(wallet_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        return wallets.detail(session, wallet_id)
    except wallets.WalletError as exc:
        raise _wallet_error(exc) from exc


@router.get("/tracked/{wallet_id}/activity", response_model=list[SwapOut])
def activity(wallet_id: str, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    try:
        wallets.get_wallet(session, wallet_id)
    except wallets.WalletError as exc:
        raise _wallet_error(exc) from exc
    return [wallets.swap_out(row) for row in wallets.activity(session, wallet_id)]


@router.post("/tracked/{wallet_id}/refresh", response_model=RefreshOut)
async def refresh(
    wallet_id: str,
    session: Session = Depends(get_session),
    sources: list[WalletActivitySource] = Depends(get_wallet_sources),
) -> dict[str, int]:
    try:
        wallet = wallets.get_wallet(session, wallet_id)
    except wallets.WalletError as exc:
        raise _wallet_error(exc) from exc
    inserted = await wallets.refresh(session, wallet, sources)
    return {"inserted": inserted}


@router.delete("/tracked/{wallet_id}", status_code=status.HTTP_204_NO_CONTENT)
def untrack(wallet_id: str, session: Session = Depends(get_session)) -> Response:
    if not wallets.untrack(session, wallet_id):
        raise HTTPException(
            status_code=404,
            detail={"code": "WALLET_NOT_FOUND", "message": f"unknown tracked wallet {wallet_id}"},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
