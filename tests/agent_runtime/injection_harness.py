"""A real platform behind the runtime's real egress, with a compromised model in front.

Nothing here is stubbed except two things, both deliberate: the model (a scripted client
whose turns are exactly what a hijacked model would emit) and the venues (a mark and a
generic quote provider, no socket). The loop, the MCP tool surface, the JSON-RPC server, the
API middleware, the scope check, the order service, the automation gate and the approval
queue are the production code.

The chain is: ``AgentLoop`` → ``McpServerToolbox`` → ``McpServer`` (the real one) →
``EzchainClient`` over an ``httpx.MockTransport`` → the real FastAPI routers on SQLite. A
test can therefore ask the money-path question directly: did a fill row appear?
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import ezchain_api.models  # noqa: F401  (register tables on Base.metadata)
from ezchain_adapters import QuoteRequest, QuoteRouter
from ezchain_adapters.registry import CHAINS, PROVIDERS
from ezchain_agent_runtime import McpError, McpTool, ToolResult
from ezchain_api.actor import ACTOR_HUMAN, OrderActor
from ezchain_api.auth import BearerAuthMiddleware, required_scope
from ezchain_api.config import Settings
from ezchain_api.db import Base
from ezchain_api.deps import get_dexscreener, get_quote_router, get_session
from ezchain_api.models import (
    ApiKeyRow,
    LedgerEntryRow,
    OrderApprovalRow,
    PaperFillRow,
    PaperOrderRow,
    StrategyRow,
    SystemFlagRow,
)
from ezchain_api.routers import agent as agent_router
from ezchain_api.routers import approvals as approvals_router
from ezchain_api.routers import buckets as buckets_router
from ezchain_api.routers import markets as markets_router
from ezchain_api.routers import paper as paper_router
from ezchain_api.routers import strategies as strategies_router
from ezchain_api.services import api_keys, buckets
from ezchain_core import PairInfo, Quote, SourceRef, TokenSnapshot
from ezchain_mcp.client import EzchainClient
from ezchain_mcp.credentials import Credential
from ezchain_mcp.server import McpServer

CHAIN = "blast"  # a paper-mark ladder: the order path runs with no network
TOKEN = "0x0000000000000000000000000000000000000abc"
PEPPER = "pepper-for-tests"
DEPLOYMENT_TOKEN = "deployment-token-admin"
AGENT_SCOPES = ("read", "trade")
PRICE = Decimal("2")
AMOUNT_USD = "25"


@dataclass(frozen=True)
class ApiCall:
    """One HTTP request the MCP server made on the agent's behalf."""

    method: str
    path: str
    status: int

    @property
    def scope(self) -> str:
        return required_scope(self.method, self.path)


class MarkedDex:
    """A DexScreener stand-in: one fresh mark per chain, no network."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    async def get_token(
        self, chain: str, address: str, *, max_age_seconds: float | None = None
    ) -> TokenSnapshot:
        return TokenSnapshot(
            chain=chain,
            address=address,
            symbol=self.symbol,
            name=self.symbol,
            price_usd=PRICE,
            liquidity_usd=Decimal("500000"),
            volume_24h_usd=Decimal("1000000"),
            price_change_m5=Decimal("0"),
            price_change_h1=Decimal("1"),
            price_change_h6=Decimal("0"),
            price_change_h24=Decimal("0"),
            pairs=(
                PairInfo(
                    "0xpair", "uniswap", "USDC", PRICE, Decimal("500000"), Decimal("1000000"), None
                ),
            ),
            source=SourceRef("dexscreener", None, datetime.now(UTC)),
            pair_created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )


class AnyVenue:
    """A quote provider that answers for every chain, so any ladder runs offline."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.calls = 0

    def supports(self, chain: Any) -> bool:
        return True

    def breaker_state(self) -> str:
        return "closed"

    async def quote(self, request: QuoteRequest) -> Quote:
        self.calls += 1
        if request.side == "buy":
            amount_in, amount_out = request.amount_usd, request.amount_usd / PRICE
        else:
            quantity = request.quantity or Decimal(0)
            amount_in, amount_out = quantity, quantity * PRICE
        return Quote(
            chain=request.chain.id,
            token_in=request.chain.quote_asset.address if request.chain.quote_asset else "USD",
            token_out=request.token_address,
            side=request.side,
            amount_in=amount_in,
            amount_out=amount_out,
            price=PRICE,
            price_impact_pct=Decimal("0"),
            fee_usd=Decimal("0"),
            kind=request.chain.quote_kind,  # type: ignore[arg-type]
            route=f"{self.kind} route",
            source=SourceRef(self.kind, None, datetime.now(UTC)),
        )


class Platform:
    """The real API routers on SQLite, served over a transport an MCP client can use."""

    def __init__(
        self,
        *,
        symbol: str = "MoonCoin",
        scopes: Sequence[str] = AGENT_SCOPES,
        strategy: Mapping[str, Any] | None = None,
        cap_usd: Decimal | None = None,
    ) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(self.engine, "connect")
        def _foreign_keys(dbapi_connection: Any, _record: Any) -> None:
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.settings = Settings(
            environment="test",
            data_mode="fixture",
            database_url="sqlite+pysqlite://",
            auto_create_schema=False,
            api_token=SecretStr(DEPLOYMENT_TOKEN),
            api_key_pepper=SecretStr(PEPPER),
        )
        self.dex = MarkedDex(symbol)
        self.venues = tuple(AnyVenue(kind) for kind in PROVIDERS)
        self.calls: list[ApiCall] = []
        self.app = FastAPI()
        self.app.add_middleware(
            BearerAuthMiddleware, settings=self.settings, session_factory=self.factory
        )
        for router in (
            paper_router.router,
            approvals_router.router,
            markets_router.router,
            strategies_router.router,
            buckets_router.router,
            agent_router.router,
        ):
            self.app.include_router(router)
        self.app.dependency_overrides[get_session] = self._session
        self.app.dependency_overrides[get_dexscreener] = lambda: self.dex
        self.app.dependency_overrides[get_quote_router] = self._router
        self.client = TestClient(self.app)
        with self.factory() as session:
            row, self.token = api_keys.create_key(
                session,
                subject="hermes",
                scopes=list(scopes),
                expires_in_days=30,
                pepper=PEPPER,
            )
            self.key_id = row.id
            if cap_usd is not None:
                buckets.set_cap(
                    session,
                    row.id,
                    cap_usd,
                    actor=OrderActor(ACTOR_HUMAN, "hermes", owner="hermes"),
                )
            if strategy is not None:
                session.add(
                    StrategyRow(
                        id="strategy-injected",
                        name=str(strategy["name"]),
                        chain=CHAIN,
                        token_address=TOKEN,
                        token_symbol=str(strategy.get("token_symbol", "TEST")),
                        kind="dca",
                        params={"order_usd": 100, "interval_minutes": 60},
                        status="active",
                    )
                )
                session.commit()

    def _session(self) -> Session:
        return self.factory()

    def _router(self) -> QuoteRouter:
        return QuoteRouter(
            providers=list(self.venues),  # type: ignore[arg-type]
            dexscreener=self.dex,  # type: ignore[arg-type]
        )

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            response = self.client.request(
                request.method,
                request.url.raw_path.decode(),
                headers=dict(request.headers),
                content=request.content,
            )
            self.calls.append(
                ApiCall(method=request.method, path=request.url.path, status=response.status_code)
            )
            return httpx.Response(
                response.status_code,
                headers=dict(response.headers),
                content=response.content,
                request=request,
            )

        return httpx.MockTransport(handler)

    def mcp_server(self, *, allow_writes: bool = True, token: str | None = None) -> McpServer:
        client = EzchainClient("http://test", token or self.token, transport=self.transport())
        credential = Credential("scoped") if allow_writes else None
        return McpServer(client, allow_writes=allow_writes, credential=credential)

    # -- another agent's money ------------------------------------------------
    def add_agent(self, subject: str, *, cap_usd: Decimal) -> str:
        """Mint a second agent key (owned by ``subject``) with a bucket, and return its id."""
        with self.factory() as session:
            row, _ = api_keys.create_key(
                session,
                subject=subject,
                scopes=list(AGENT_SCOPES),
                expires_in_days=30,
                pepper=PEPPER,
            )
            buckets.set_cap(
                session,
                row.id,
                cap_usd,
                actor=OrderActor(ACTOR_HUMAN, subject, owner=subject),
            )
            return row.id

    def read(self, path: str) -> httpx.Response:
        """One request as the agent key, over the same middleware the MCP server uses."""
        return self.client.get(path, headers={"authorization": f"Bearer {self.token}"})

    # -- what actually happened to the money ---------------------------------
    def fills(self) -> tuple[PaperFillRow, ...]:
        with self.factory() as session:
            return tuple(session.scalars(select(PaperFillRow)))

    def orders(self) -> tuple[PaperOrderRow, ...]:
        with self.factory() as session:
            return tuple(session.scalars(select(PaperOrderRow)))

    def approvals(self) -> tuple[OrderApprovalRow, ...]:
        with self.factory() as session:
            return tuple(session.scalars(select(OrderApprovalRow)))

    def cash(self) -> Decimal:
        with self.factory() as session:
            rows = session.scalars(
                select(LedgerEntryRow).where(LedgerEntryRow.account == "cash:USD")
            )
            return sum((row.amount for row in rows), Decimal(0))

    def ledger_groups(self) -> frozenset[str]:
        """Every journal group id written. The genesis group is the only allowed one."""
        with self.factory() as session:
            return frozenset(session.scalars(select(LedgerEntryRow.group_id)))

    def key_scopes(self) -> frozenset[str]:
        with self.factory() as session:
            row = session.get(ApiKeyRow, self.key_id)
            assert row is not None
            return frozenset(row.scopes or ())

    def key_count(self) -> int:
        with self.factory() as session:
            return len(tuple(session.scalars(select(ApiKeyRow.id))))

    def switch_active(self) -> bool:
        with self.factory() as session:
            row = session.get(SystemFlagRow, "engine_kill_switch")
            return bool((row.value or {}).get("active", False)) if row is not None else False

    def close(self) -> None:
        self.engine.dispose()


class McpServerToolbox:
    """The loop's egress, speaking JSON-RPC to the real server in-process."""

    def __init__(self, server: McpServer) -> None:
        self.server = server
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.refusals: list[str] = []
        self._next_id = 0

    def _request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        response = self.server.handle(
            {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": dict(params)}
        )
        if not isinstance(response, dict):  # pragma: no cover - one request, one response
            raise McpError("the server answered a batch")
        if "error" in response:
            self.refusals.append(f"{params.get('name', method)}: {response['error']['message']}")
            raise McpError(f"{method} failed: {response['error']}")
        result: dict[str, Any] = response["result"]
        return result

    def list_tools(self) -> Sequence[McpTool]:
        result = self._request("tools/list", {})
        return tuple(
            McpTool(
                name=item["name"],
                description=item["description"],
                input_schema=item["inputSchema"],
            )
            for item in result["tools"]
        )

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        self.calls.append((name, dict(arguments)))
        result = self._request("tools/call", {"name": name, "arguments": dict(arguments)})
        data = result.get("structuredContent")
        if data is None:  # pragma: no cover - the server always sends structured content
            data = json.loads(result["content"][0]["text"])
        return ToolResult(tool=name, data=data, is_error=bool(result.get("isError")))


def chains() -> tuple[str, ...]:
    """Every chain the registry declares, so a case runs against the whole ladder set."""
    return tuple(sorted(meta.id for meta in CHAINS))


def outcome_text(outcome: Any) -> str:
    """Everything a session would show: its detail, its answer and every observation."""
    parts = [outcome.detail or ""]
    if outcome.answer is not None:
        parts.append(outcome.answer.render())
    parts.extend(
        json.dumps(observation.data, default=str, sort_keys=True)
        for observation in outcome.observations
    )
    return "\n".join(parts)


__all__ = [
    "AGENT_SCOPES",
    "AMOUNT_USD",
    "ApiCall",
    "CHAIN",
    "DEPLOYMENT_TOKEN",
    "McpServerToolbox",
    "Platform",
    "TOKEN",
    "chains",
    "outcome_text",
]
