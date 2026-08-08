"""Preflight checks — verifies readiness before upgrade or deployment."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass


@dataclass
class CheckResult:
    name: str
    passed: bool
    level: str  # "FAIL" or "WARN"
    detail: str = ""


async def _check_database() -> CheckResult:
    try:
        from sqlalchemy import text

        from hecate.core.database import async_session_factory

        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return CheckResult(name="database", passed=True, level="FAIL", detail="OK")
    except Exception as exc:
        return CheckResult(name="database", passed=False, level="FAIL", detail=str(exc))


async def _check_alembic_head() -> CheckResult:
    try:
        import subprocess

        result = subprocess.run(["alembic", "current"], capture_output=True, text=True, check=False, timeout=10)
        if result.returncode != 0:
            return CheckResult(name="alembic_head", passed=False, level="FAIL", detail=result.stderr.strip())
        return CheckResult(name="alembic_head", passed=True, level="FAIL", detail=result.stdout.strip())
    except Exception as exc:
        return CheckResult(name="alembic_head", passed=False, level="WARN", detail=str(exc))


async def _check_redis() -> CheckResult:
    try:
        from hecate.core.config import settings

        if settings.SESSION_STATE_STORE_BACKEND == "memory":
            return CheckResult(name="redis", passed=True, level="WARN", detail="memory backend, Redis not required")
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.SESSION_STATE_REDIS_URL)
        await client.ping()
        await client.aclose()
        return CheckResult(name="redis", passed=True, level="WARN", detail="OK")
    except Exception as exc:
        return CheckResult(name="redis", passed=False, level="WARN", detail=str(exc))


async def _check_disk_space() -> CheckResult:
    try:
        usage = shutil.disk_usage("/")
        free_gb = usage.free / (1024**3)
        if free_gb < 1.0:
            return CheckResult(name="disk_space", passed=False, level="FAIL", detail=f"{free_gb:.1f} GB free")
        return CheckResult(name="disk_space", passed=True, level="FAIL", detail=f"{free_gb:.1f} GB free")
    except Exception as exc:
        return CheckResult(name="disk_space", passed=False, level="WARN", detail=str(exc))


async def _check_env_vars() -> CheckResult:
    required = ["DATABASE_URL"]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        return CheckResult(name="env_vars", passed=False, level="FAIL", detail=f"missing: {', '.join(missing)}")
    return CheckResult(name="env_vars", passed=True, level="FAIL", detail="all present")


async def run_checks() -> list[CheckResult]:
    """Run all preflight checks and return results."""
    checks = await asyncio.gather(
        _check_database(),
        _check_alembic_head(),
        _check_redis(),
        _check_disk_space(),
        _check_env_vars(),
    )
    return list(checks)


import asyncio  # noqa: E402
