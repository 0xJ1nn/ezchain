"""Health, readiness, metrics, chain registry and build-source provenance.

``/healthz`` and ``/readyz`` are public orchestration probes. ``/health`` and ``/metrics``
describe the process, the scheduler and paper order counts, so the auth middleware serves
them only to a loopback client or to a request carrying the deployment token. The
build-source route verifies a signed attestation and reports why a build is not official.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
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
from ..version import PRODUCT_VERSION

router = APIRouter(tags=["system"])

PAPER_ORDER_STATUSES = ("pending", "pending_approval", "filled", "rejected")
METRICS_MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"

OFFICIAL_REPOSITORY = "https://github.com/0xJ1nn/ezchain"
DEFAULT_PUBLIC_KEY_PATH = "tools/release/official-public.pem"
DEFAULT_ATTESTATION_PATH = "release/attestation.json"
VERIFY_TIMEOUT_SECONDS = 10
# Copied from the release notes at deployment time, never committed. A build cannot
# authenticate its own key; the operator pins the fingerprint the maintainer published.
RELEASE_KEY_FINGERPRINT_ENV = "EZCHAIN_RELEASE_KEY_FINGERPRINT"

BuildSourceStatus = Literal[
    "verified",
    "no_public_key",
    "no_attestation",
    "no_signature",
    "malformed_attestation",
    "repository_mismatch",
    "invalid_signature",
    "key_not_pinned",
    "key_fingerprint_mismatch",
    "verifier_unavailable",
]

BUILD_SOURCE_MESSAGES: dict[BuildSourceStatus, str] = {
    "verified": "This build's attestation is signed by the pinned official release key.",
    "no_public_key": (
        "No release public key is configured for this installation, so the build source "
        "cannot be checked."
    ),
    "no_attestation": "This installation carries no build attestation.",
    "no_signature": "This installation's build attestation has no signature.",
    "malformed_attestation": "This installation's build attestation is not a valid record.",
    "repository_mismatch": "The signed attestation names a repository other than the official one.",
    "invalid_signature": "The build attestation does not verify against the release public key.",
    "key_not_pinned": (
        "No official release-key fingerprint is configured for this installation, so the "
        "installed key cannot be authenticated."
    ),
    "key_fingerprint_mismatch": (
        "The installed release key does not match the fingerprint published for official builds."
    ),
    "verifier_unavailable": (
        "Signature verification needs OpenSSL, which this host does not provide."
    ),
}


class BuildSourceOut(BaseModel):
    """The signed-provenance verdict, with a reason when the build is not official."""

    official: bool
    status: BuildSourceStatus
    message: str
    tag: str | None = None


def _build_source_paths() -> tuple[Path, Path]:
    """Locate the published public key and the running build's attestation.

    Operators set absolute paths in production. Relative defaults keep a source checkout
    honest: with no published key and no signed attestation it reports an unverified build.
    """
    key = Path(os.environ.get("EZCHAIN_RELEASE_PUBLIC_KEY", DEFAULT_PUBLIC_KEY_PATH))
    attestation = Path(os.environ.get("EZCHAIN_BUILD_ATTESTATION", DEFAULT_ATTESTATION_PATH))
    return key, attestation


def _build_source_verdict(status: BuildSourceStatus, tag: str | None = None) -> BuildSourceOut:
    return BuildSourceOut(
        official=status == "verified",
        status=status,
        message=BUILD_SOURCE_MESSAGES[status],
        tag=tag,
    )


def _normalize_fingerprint(value: str) -> str:
    """Drop separators and casing so a colon-separated fingerprint still matches."""
    return "".join(char for char in value.lower() if char in "0123456789abcdef")


def _expected_fingerprint() -> str | None:
    """The maintainer-supplied fingerprint, normalised; unset means fail closed."""
    return _normalize_fingerprint(os.environ.get(RELEASE_KEY_FINGERPRINT_ENV, "").strip()) or None


def _key_fingerprint(public_key: Path) -> str | None:
    """SHA-256 of the DER-encoded public key, matching the fingerprint in release notes."""
    try:
        result = subprocess.run(
            ["openssl", "pkey", "-pubin", "-in", str(public_key), "-outform", "DER"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=VERIFY_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return hashlib.sha256(result.stdout).hexdigest()


def build_source_status() -> BuildSourceOut:
    """Verify the on-disk attestation against the pinned key; fail closed on any gap.

    The attestation is untrusted until its signature checks out, and the installed key is
    untrusted until its fingerprint matches the value the maintainer published with the
    release. Only then does its repository claim count, so a fork shipping its own key
    cannot name itself official. This response never exposes key material or a signature.
    """
    public_key, attestation = _build_source_paths()
    if not public_key.is_file():
        return _build_source_verdict("no_public_key")
    if not attestation.is_file():
        return _build_source_verdict("no_attestation")
    signature = Path(f"{attestation}.sig")
    if not signature.is_file():
        return _build_source_verdict("no_signature")

    try:
        document = json.loads(attestation.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _build_source_verdict("malformed_attestation")
    if (
        not isinstance(document, dict)
        or not isinstance(document.get("repository"), str)
        or not isinstance(document.get("tag"), str)
    ):
        return _build_source_verdict("malformed_attestation")

    if shutil.which("openssl") is None:
        return _build_source_verdict("verifier_unavailable")
    expected = _expected_fingerprint()
    if expected is None:
        return _build_source_verdict("key_not_pinned")
    fingerprint = _key_fingerprint(public_key)
    if fingerprint is None:
        return _build_source_verdict("verifier_unavailable")
    if fingerprint != expected:
        return _build_source_verdict("key_fingerprint_mismatch")
    try:
        verified = (
            subprocess.run(
                [
                    "openssl",
                    "dgst",
                    "-sha256",
                    "-verify",
                    str(public_key),
                    "-signature",
                    str(signature),
                    str(attestation),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=VERIFY_TIMEOUT_SECONDS,
                check=False,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return _build_source_verdict("verifier_unavailable")
    if not verified:
        return _build_source_verdict("invalid_signature")
    if document["repository"] != OFFICIAL_REPOSITORY:
        return _build_source_verdict("repository_mismatch")
    return _build_source_verdict("verified", tag=document["tag"])


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
    return {"status": "ok", "mode": mode_status(settings), "version": PRODUCT_VERSION}


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
        "version": PRODUCT_VERSION,
    }
    return JSONResponse(status_code=200 if ok else 503, content=body)


@router.get("/api/v1/chains", response_model=list[ChainOut])
def chains() -> list[ChainOut]:
    return [ChainOut.model_validate(chain.to_dict()) for chain in CHAINS]


@router.get("/api/v1/system/build-source", response_model=BuildSourceOut)
def build_source() -> BuildSourceOut:
    """Signed-provenance verdict for this installation, with the reason for a refusal."""
    return build_source_status()


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
