"""Health check and version endpoint tests."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import hecate.main
from hecate.main import _handle_sigterm, app


@pytest.fixture
def client():
    return TestClient(app)


class TestHealthLive:
    def test_returns_alive(self, client: TestClient):
        r = client.get("/health/live")
        assert r.status_code == 200
        assert r.json() == {"status": "alive"}

    def test_no_external_dependency_check(self, client: TestClient):
        """Even if DB is configured, /health/live must not check it."""
        r = client.get("/health/live")
        assert r.status_code == 200
        assert "database" not in r.json()


class TestHealthReady:
    def test_returns_ready_when_all_healthy(self, client: TestClient):
        r = client.get("/health/ready")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ready"
        assert data["checks"]["draining"] is True
        assert data["checks"]["database"] is True

    def test_returns_503_when_draining(self, client: TestClient):
        original = hecate.main.SHOULD_ACCEPT_TRAFFIC
        hecate.main.SHOULD_ACCEPT_TRAFFIC = False
        try:
            r = client.get("/health/ready")
            assert r.status_code == 503
            data = r.json()
            assert data["status"] == "not_ready"
            assert "draining" in data["failed"]
        finally:
            hecate.main.SHOULD_ACCEPT_TRAFFIC = original


class TestHealthStartup:
    def test_returns_503_before_startup(self, client: TestClient):
        original = hecate.main._APP_STARTUP_COMPLETE
        hecate.main._APP_STARTUP_COMPLETE = False
        try:
            r = client.get("/health/startup")
            assert r.status_code == 503
            data = r.json()
            assert data["status"] == "starting"
            assert data["startup_complete"] is False
        finally:
            hecate.main._APP_STARTUP_COMPLETE = original

    def test_returns_200_after_startup(self, client: TestClient):
        original = hecate.main._APP_STARTUP_COMPLETE
        hecate.main._APP_STARTUP_COMPLETE = True
        try:
            r = client.get("/health/startup")
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "started"
            assert data["startup_complete"] is True
        finally:
            hecate.main._APP_STARTUP_COMPLETE = original


class TestVersion:
    def test_returns_build_info(self, client: TestClient):
        r = client.get("/version")
        assert r.status_code == 200
        data = r.json()
        assert "version" in data
        assert "commit" in data
        assert "alembic_head" in data
        assert "python" in data
        assert "build_date" in data

    def test_unset_env_vars_return_unknown(self, client: TestClient):
        r = client.get("/version")
        data = r.json()
        assert data["commit"] == "unknown"
        assert data["build_date"] == "unknown"


class TestSIGTERMShutdown:
    """SIGTERM signal triggers graceful shutdown by flipping SHOULD_ACCEPT_TRAFFIC."""

    def test_sigterm_flips_draining_flag(self):
        original = hecate.main.SHOULD_ACCEPT_TRAFFIC
        try:
            hecate.main.SHOULD_ACCEPT_TRAFFIC = True
            _handle_sigterm(15, None)
            assert hecate.main.SHOULD_ACCEPT_TRAFFIC is False
        finally:
            hecate.main.SHOULD_ACCEPT_TRAFFIC = original

    def test_drain_returns_when_no_active_requests(self):
        original = hecate.main.ACTIVE_REQUESTS
        try:
            hecate.main.ACTIVE_REQUESTS = 0
            asyncio.run(hecate.main._drain_active_requests(timeout=1))
        finally:
            hecate.main.ACTIVE_REQUESTS = original
