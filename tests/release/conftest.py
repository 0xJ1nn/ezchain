"""A bare client for the build-source route, which has no database dependency."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ezchain_api.routers import system


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(system.router)
    return TestClient(app)
