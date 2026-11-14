"""Health, readiness, metrics and chain registry.

``/healthz`` and ``/readyz`` are public orchestration probes. ``/health`` and ``/metrics``
describe the process, the scheduler and paper order counts, so the auth middleware serves
them only to a loopback client or to a request carrying the deployment token.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ezchain_adapters import CHAINS

from ..config import Settings, get_settings
from ..db import database_revision, head_revision
from ..deps import get_session
from ..mode import mode_status
from ..models import PaperFillRow, PaperOrderRow
from ..observability import HTTP_METRICS, reject_series, render_prometheus
from ..schemas import ChainOut, KillSwitchIn, KillSwitchOut
from ..services.ops_signals import build_ops_snapshot
from ..services.system_flags import read_kill_switch, set_kill_switch

router = APIRouter(tags=["system"])

PAPER_ORDER_STATUSES = ("pending", "pending_approval", "filled", "rejected")
METRICS_MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _rollback_quietly(session: Session) -> None:
    """A broken connection can also fail to roll back; readiness must still answer."""
    try:
        session.rollback()
    except Exception:
        pass


@router.get("/healthz")
def healthz(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Liveness: the process is up. Never touches the database.

    The mode is the same derived value the capabilities document reports, so a probe and
    an agent cannot disagree about whether this deployment can trade.
    """
    return {"status": "ok", "mode": mode_status(settings)}


@router.get("/readyz")
def readyz(
    session: Session = Depends(get_session), settings: Settings = Depends(get_settings)
) -> JSONResponse:
    """Readiness: database reachable and schema exactly at the Alembic head.

    The desktop build bootstraps its embedded SQLite schema with ``create_all`` and has no
    Alembic revision row. That is a supported deployment mode, not a missing migration;
    it reports ``migration: embedded`` and is ready once the database answers.
    """
    db_ok = True
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        _rollback_quietly(session)
        db_ok = False

    if (
        settings.environment == "desktop"
        and settings.database_url.startswith("sqlite")
        and db_ok
        and settings.auto_create_schema
    ):
        return JSONResponse(
            status_code=200,
            content={
                "status": "ready",
                "db": "ok",
                "migration": "embedded",
                "revision": None,
                "head": None,
            },
        )

    revision = database_revision(session) if db_ok else None
    try:
        head = head_revision()
    except Exception:
        head = None
    at_head = db_ok and revision is not None and head is not None and revision == head
    if at_head:
        migration = "head"
    elif not db_ok or head is None:
        migration = "unknown"
    elif revision is None:
        migration = "missing"
    else:
        migration = "pending"

    body = {
        "status": "ready" if at_head else "not_ready",
        "db": "ok" if db_ok else "down",
        "migration": migration,
        "revision": revision,
        "head": head,
    }
    return JSONResponse(status_code=200 if at_head else 503, content=body)


@router.get("/metrics")
def metrics(request: Request, session: Session = Depends(get_session)) -> Response:
    """Prometheus text exposition with bounded labels. No third-party dependency.

    A loopback scrape needs no credential; a scrape from another host sends the
    deployment token (middleware). The operational families are sampled from live state;
    when the database cannot be read they are omitted rather than reported as zero.
    """
    up = True
    counts = {status: 0 for status in PAPER_ORDER_STATUSES}
    fills = 0
    rejected: dict[object, int] = {}
    try:
        session.execute(text("SELECT 1"))
        rows = session.execute(
            select(PaperOrderRow.status, func.count()).group_by(PaperOrderRow.status)
        ).all()
        for status, count in rows:
            key = status if status in counts else "other"
            counts[key] = counts.get(key, 0) + int(count)
        fills = int(session.scalar(select(func.count()).select_from(PaperFillRow)) or 0)
        # A refused order persists the code of the first gate that refused it, so this
        # grouping is the database's own answer to "why was this order rejected" rather
        # than a count of what this process happened to observe. An unreadable database
        # reports no rejects and ``ezchain_up 0`` explains the absence.
        rejected = {
            reason: int(count)
            for reason, count in session.execute(
                select(PaperOrderRow.reason, func.count())
                .where(PaperOrderRow.status == "rejected")
                .group_by(PaperOrderRow.reason)
            ).all()
        }
    except Exception:
        _rollback_quietly(session)
        up = False
    ops = None
    rejects = None
    if up:
        router = getattr(request.app.state, "quote_router", None)
        ops = build_ops_snapshot(
            session,
            quote_router=router,
            market_monitor=getattr(request.app.state, "market_monitor", None),
            provider_budget=getattr(request.app.state, "provider_budget", None),
            # The same policy the order gate classifies with, so the budget reported as
            # the alert threshold is the one a refused read was measured against.
            policy=getattr(router, "freshness", None),
        )
        # One series per reason code, each key normalised against the taxonomy, so the
        # exposition is the persisted answer with a bounded label set. When the database
        # is unreadable this stays ``None`` and the family is omitted: an empty rejects
        # family must never be read as "no order was rejected".
        rejects = reject_series(rejected)
    return Response(
        content=render_prometheus(
            HTTP_METRICS,
            up=up,
            paper_orders=counts,
            paper_fills=fills,
            ops=ops,
            rejects=rejects,
        ),
        media_type=METRICS_MEDIA_TYPE,
    )


@router.get("/health")
def health(
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Compatibility health endpoint: 503 when the database is down."""
    try:
        session.execute(text("SELECT 1"))
        db = "ok"
    except Exception:
        _rollback_quietly(session)
        db = "down"
    ok = db == "ok"
    body = {
        "status": "ok" if ok else "degraded",
        "db": db,
        "mode": settings.data_mode,
        "engine_inline": settings.engine_inline,
        "market_scan_inline": settings.market_scan_inline,
        "market_monitor": getattr(request.app.state, "market_monitor", None) is not None,
        "version": "0.1.0",
    }
    return JSONResponse(status_code=200 if ok else 503, content=body)


@router.get("/api/v1/chains", response_model=list[ChainOut])
def chains() -> list[ChainOut]:
    return [ChainOut.model_validate(chain.to_dict()) for chain in CHAINS]


@router.get("/api/v1/system/kill-switch", response_model=KillSwitchOut)
def get_kill_switch(
    session: Session = Depends(get_session), settings: Settings = Depends(get_settings)
) -> KillSwitchOut:
    """Operator switch with two levels over one flag. Persisted in ``system_flags``."""
    return KillSwitchOut.model_validate(read_kill_switch(session, settings).to_dict())


@router.post("/api/v1/system/kill-switch", response_model=KillSwitchOut)
def update_kill_switch(
    payload: KillSwitchIn, session: Session = Depends(get_session)
) -> KillSwitchOut:
    """Arm or release the switch.

    Mutating routes require ``Authorization: Bearer <EZCHAIN_API_TOKEN>`` (middleware);
    the response carries the persisted state and ``updated_at`` (the audit timestamp).
    The token identifies the deployment, not an individual operator.
    """
    state = set_kill_switch(session, active=payload.active, reason=payload.reason)
    return KillSwitchOut.model_validate(state.to_dict())
