"""Preflight REST API."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter

from hecate.services.preflight import run_checks

router = APIRouter(prefix="/api", tags=["preflight"])


@router.get("/preflight")
async def preflight() -> dict:
    results = await run_checks()
    checks = [asdict(r) for r in results]
    failures = [r["name"] for r in checks if not r["passed"] and r["level"] == "FAIL"]
    return {"checks": checks, "ready": len(failures) == 0, "failures": failures}
