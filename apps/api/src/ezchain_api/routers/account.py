"""Read-only ledger, performance, limits, alerts and CSV export routes.

Nothing in this module mutates the ledger. Every number is computed server-side from
``ledger_entries`` plus the existing paper order/fill rows (CONTRACT I1, I10).
"""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..deps import get_session
from ..schemas_account import (
    AttributionRowOut,
    LedgerEntryOut,
    PaperAlertsOut,
    PaperLimitsOut,
    PaperPerformanceOut,
)
from ..services import account_read

router = APIRouter(prefix="/api/v1/paper", tags=["paper"])

ORDER_COLUMNS = (
    "id",
    "chain",
    "token_address",
    "side",
    "amount_usd",
    "status",
    "reason",
    "quote_kind",
    "created_at",
    "filled_at",
)
FILL_COLUMNS = (
    "id",
    "order_id",
    "chain",
    "token_address",
    "side",
    "quantity",
    "price_usd",
    "notional_usd",
    "fee_usd",
    "quote_kind",
    "created_at",
)
LEDGER_COLUMNS = (
    "id",
    "group_id",
    "account",
    "asset",
    "amount",
    "usd_value",
    "kind",
    "created_at",
)
FORMULA_PREFIXES = ("=", "+", "-", "@")


def _csv_cell(value: Any) -> str:
    """String cell with spreadsheet formula injection neutralised.

    A leading apostrophe is the documented, visible way to keep a spreadsheet from
    evaluating attacker-controlled text. It applies to every cell, including negative
    numbers, so the export is never a formula surface.
    """
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(FORMULA_PREFIXES) else text


def _csv_response(filename: str, columns: tuple[str, ...], rows: list[dict[str, Any]]) -> Response:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_csv_cell(row.get(column)) for column in columns])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/ledger", response_model=list[LedgerEntryOut])
def ledger_entries(
    limit: int = Query(default=200, ge=1, le=500),
    account: str | None = Query(default=None, max_length=160),
    kind: str | None = Query(default=None, max_length=32),
    session: Session = Depends(get_session),
) -> list[LedgerEntryOut]:
    """Newest-first journal slice, optionally filtered by account and entry kind."""
    rows = account_read.ledger(session, limit=limit, account=account, kind=kind)
    return [LedgerEntryOut.model_validate(row) for row in rows]


@router.get("/performance", response_model=PaperPerformanceOut)
def performance(session: Session = Depends(get_session)) -> PaperPerformanceOut:
    """PAPER cash, equity, realized/unrealized PnL, trade counts and daily realized."""
    return PaperPerformanceOut.model_validate(account_read.performance(session))


@router.get("/attribution", response_model=list[AttributionRowOut])
def attribution(session: Session = Depends(get_session)) -> list[AttributionRowOut]:
    """Per-token realized PnL and its share of absolute realized PnL."""
    return [AttributionRowOut.model_validate(row) for row in account_read.attribution(session)]


@router.get("/limits", response_model=PaperLimitsOut)
def limits(
    session: Session = Depends(get_session), settings: Settings = Depends(get_settings)
) -> PaperLimitsOut:
    """The risk limits the UI may show, plus the persisted operator switch."""
    return PaperLimitsOut.model_validate(account_read.limits(session, settings))


@router.get("/alerts", response_model=PaperAlertsOut)
def alerts(
    session: Session = Depends(get_session), settings: Settings = Depends(get_settings)
) -> PaperAlertsOut:
    """Derived operator alerts. Empty list means the account is clean."""
    return PaperAlertsOut.model_validate(account_read.alerts(session, settings))


@router.get("/export/orders.csv")
def export_orders(session: Session = Depends(get_session)) -> Response:
    return _csv_response("paper-orders.csv", ORDER_COLUMNS, account_read.export_orders(session))


@router.get("/export/fills.csv")
def export_fills(session: Session = Depends(get_session)) -> Response:
    return _csv_response("paper-fills.csv", FILL_COLUMNS, account_read.export_fills(session))


@router.get("/export/ledger.csv")
def export_ledger(session: Session = Depends(get_session)) -> Response:
    return _csv_response("paper-ledger.csv", LEDGER_COLUMNS, account_read.export_ledger(session))
