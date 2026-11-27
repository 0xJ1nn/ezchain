"""The streaming edge: one SSE or WebSocket channel per agent session.

Both transports tail the same append-only trace through the stream hub. A client names the
last event id it saw and receives only the later events, in order. The principal is resolved
with the same scoped-key helpers as any other route, and the channel's owner and required
scope are checked before a single event is sent.

A WebSocket upgrade is authenticated by a bearer on the handshake header, so this transport is
for server-side clients: a browser cannot set that header on a WebSocket. Browser clients must
reach the stream through a server-side relay that holds the scoped key. The same is true of a
browser EventSource, which cannot send an Authorization header either.
"""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.applications import Starlette

from ..config import Settings, get_settings
from ..deps import get_session
from ..services import api_keys
from ..services.stream import Lagged, StreamError, StreamHub

router = APIRouter(prefix="/api/v1/stream", tags=["stream"])


def _hub(app: Starlette) -> StreamHub:
    hub = getattr(app.state, "stream_hub", None)
    if hub is None:
        hub = StreamHub()
        app.state.stream_hub = hub
    return hub


def get_stream_hub(request: Request) -> StreamHub:
    return _hub(request.app)


def get_ws_hub(websocket: WebSocket) -> StreamHub:
    return _hub(websocket.app)


def _bearer(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, separator, value = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not value:
        return None
    return value.strip() or None


def _resolve_principal(
    token: str | None, session: Session, settings: Settings
) -> api_keys.Principal | None:
    if token is None:
        return None
    expected = None if settings.api_token is None else settings.api_token.get_secret_value()
    if expected is not None and hmac.compare_digest(
        token.encode("utf-8"), expected.encode("utf-8")
    ):
        return api_keys.Principal(
            subject="deployment", scopes=frozenset(api_keys.SCOPES), legacy=True
        )
    pepper = None if settings.api_key_pepper is None else settings.api_key_pepper.get_secret_value()
    row = api_keys.verify_token(session, token, pepper)
    if row is None:
        return None
    return api_keys.Principal(
        subject=row.subject,
        scopes=frozenset(row.scopes or []),
        key_id=row.id,
    )


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={
            "code": "UNAUTHORIZED",
            "message": "a valid scoped key is required to stream",
            "retryable": False,
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_stream_principal(
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> api_keys.Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, api_keys.Principal):
        principal = _resolve_principal(
            _bearer(request.headers.get("authorization")), session, settings
        )
    if principal is None:
        raise _unauthorized()
    return principal


async def require_ws_principal(
    websocket: WebSocket,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> api_keys.Principal:
    principal = _resolve_principal(
        _bearer(websocket.headers.get("authorization")), session, settings
    )
    if principal is None:
        raise WebSocketException(code=1008, reason="UNAUTHORIZED")
    return principal


def _origin_allowed(origin: str | None, settings: Settings) -> bool:
    if origin is None:
        return True
    allowed = {item.rstrip("/") for item in settings.cors_origins}
    return origin.rstrip("/") in allowed


def _http_error(exc: StreamError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail())


def _last_id(request: Request, query_value: str | None) -> str | None:
    return query_value if query_value is not None else request.headers.get("last-event-id")


@router.get("/{session_id}")
async def stream_events(
    session_id: str,
    request: Request,
    principal: api_keys.Principal = Depends(require_stream_principal),
    hub: StreamHub = Depends(get_stream_hub),
    last_event_id: str | None = Query(default=None),
) -> StreamingResponse:
    """Tail a session's trace as Server-Sent Events.

    Send ``Last-Event-ID`` (or ``last_event_id``) on reconnect to receive strictly later
    events. Each frame's ``id`` is that event's sequence; its ``data`` is a JSON envelope with
    ``id``, ``session_id``, ``sequence``, ``schema``, ``kind``, ``at``, ``fields``, ``prev`` and
    ``hash``, matching the hash-chained trace record.
    """
    try:
        snapshot, queue = hub.subscribe(session_id, principal, _last_id(request, last_event_id))
    except StreamError as exc:
        raise _http_error(exc) from exc

    async def frames() -> AsyncIterator[str]:
        try:
            for event in snapshot:
                yield event.sse()
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, Lagged):
                    yield item.sse()
                    break
                yield item.sse()
        finally:
            hub.unsubscribe(session_id, queue)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.websocket("/{session_id}")
async def stream_socket(
    websocket: WebSocket,
    session_id: str,
    principal: api_keys.Principal = Depends(require_ws_principal),
    hub: StreamHub = Depends(get_ws_hub),
    settings: Settings = Depends(get_settings),
    last_event_id: str | None = Query(default=None),
) -> None:
    if not _origin_allowed(websocket.headers.get("origin"), settings):
        await websocket.close(code=1008, reason="origin not allowed")
        return

    supplied = (
        last_event_id if last_event_id is not None else websocket.query_params.get("last_event_id")
    )
    try:
        snapshot, queue = hub.subscribe(session_id, principal, supplied)
    except StreamError as exc:
        await websocket.accept()
        await websocket.send_json({"error": exc.detail()})
        await websocket.close(code=1008)
        return

    await websocket.accept()
    try:
        for event in snapshot:
            await websocket.send_json(event.envelope())
        while True:
            item = await queue.get()
            if item is None:
                break
            await websocket.send_json(item.envelope())
            if isinstance(item, Lagged):
                break
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(session_id, queue)


__all__ = [
    "get_stream_hub",
    "get_ws_hub",
    "require_stream_principal",
    "require_ws_principal",
    "router",
]
