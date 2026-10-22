"""Reconciliation HTTP surface: read-only and schema-shaped."""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ezchain_api.routers import paper as paper_router
from ezchain_api.services import paper


def test_reconciliation_endpoint_reports_a_clean_account(
    client_for, session: Session, settings
) -> None:
    paper.ensure_genesis(session, Decimal("10000"))
    client: TestClient = client_for(paper_router.router)

    response = client.get("/api/v1/paper/reconciliation")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["totals"]["cash_usd"] == "10000"
    assert body["totals"]["fills"] == 0
    assert body["checks"]
    assert all(check["ok"] for check in body["checks"])
    assert body["as_of"]
