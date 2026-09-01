"""Inference endpoint management — ABC, OpenAI backend, health polling.

Provides InferenceBackendABC for pluggable inference backends,
OpenAICompatibleBackend as the builtin implementation, and
InferenceManager for endpoint lifecycle management.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.inference_endpoint import InferenceEndpointModel

logger = logging.getLogger(__name__)


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"


class InferenceBackendABC(ABC):
    """Abstract interface for inference backend interactions."""

    @abstractmethod
    async def health_check(self, endpoint_url: str, timeout: float = 10.0) -> HealthStatus:
        """Check if the inference endpoint is healthy.

        Args:
            endpoint_url: Base URL of the inference endpoint.
            timeout: Request timeout in seconds.

        Returns:
            HealthStatus indicating endpoint health.
        """
        ...

    @abstractmethod
    async def invoke(
        self,
        endpoint_url: str,
        request: dict[str, Any],
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Send an inference request to the endpoint.

        Args:
            endpoint_url: Base URL of the inference endpoint.
            request: The inference request payload.
            timeout: Request timeout in seconds.

        Returns:
            Response dict from the inference endpoint.
        """
        ...


class OpenAICompatibleBackend(InferenceBackendABC):
    """Inference backend for OpenAI-compatible endpoints (vLLM, Ollama, etc.)."""

    async def health_check(self, endpoint_url: str, timeout: float = 10.0) -> HealthStatus:
        url = endpoint_url.rstrip("/")
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(f"{url}/health", timeout=timeout)
                if resp.status_code == 200:
                    return HealthStatus.HEALTHY
                return HealthStatus.DEGRADED
            except (httpx.TimeoutException, httpx.ConnectError):
                return HealthStatus.UNREACHABLE

    async def invoke(
        self,
        endpoint_url: str,
        request: dict[str, Any],
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        url = endpoint_url.rstrip("/")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{url}/v1/chat/completions",
                json=request,
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()


class InferenceManager:
    """Service for inference endpoint lifecycle management."""

    def __init__(self, db: AsyncSession, backend: InferenceBackendABC | None = None) -> None:
        self._db = db
        self._backend = backend or OpenAICompatibleBackend()
        self._poll_task: asyncio.Task | None = None
        self._retry_attempts = 3
        self._poll_interval = 30.0

    async def create_endpoint(
        self,
        url: str,
        model_id: str,
        backend_type: str = "openai-compatible",
        auth_config: dict | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> InferenceEndpointModel:
        ws_id = workspace_id or uuid.UUID("00000000-0000-0000-0000-000000000000")
        endpoint = InferenceEndpointModel(
            url=url,
            model_id=model_id,
            backend_type=backend_type,
            auth_config=auth_config or {},
            health_status=HealthStatus.HEALTHY,
            workspace_id=ws_id,
        )
        self._db.add(endpoint)
        await self._db.flush()
        return endpoint

    async def list_endpoints(
        self,
        workspace_id: uuid.UUID | None = None,
    ) -> list[InferenceEndpointModel]:
        ws_id = workspace_id or uuid.UUID("00000000-0000-0000-0000-000000000000")
        stmt = (
            select(InferenceEndpointModel)
            .where(
                InferenceEndpointModel.workspace_id == ws_id,
                ~InferenceEndpointModel.deleted,
            )
            .order_by(InferenceEndpointModel.model_id)
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_endpoint(self, endpoint_id: uuid.UUID) -> InferenceEndpointModel | None:
        stmt = select(InferenceEndpointModel).where(
            InferenceEndpointModel.id == endpoint_id,
            ~InferenceEndpointModel.deleted,
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_endpoint(self, endpoint_id: uuid.UUID) -> bool:
        endpoint = await self.get_endpoint(endpoint_id)
        if endpoint is None:
            return False
        endpoint.deleted = True
        endpoint.deleted_at = datetime.now(UTC)
        await self._db.flush()
        return True

    async def check_health(self, endpoint_id: uuid.UUID) -> HealthStatus:
        endpoint = await self.get_endpoint(endpoint_id)
        if endpoint is None:
            return HealthStatus.UNREACHABLE

        for attempt in range(self._retry_attempts):
            status = await self._backend.health_check(endpoint.url)
            if status == HealthStatus.HEALTHY:
                endpoint.health_status = HealthStatus.HEALTHY
                endpoint.last_health_at = datetime.now(UTC)
                await self._db.flush()
                return HealthStatus.HEALTHY
            if status == HealthStatus.DEGRADED and attempt < self._retry_attempts - 1:
                continue

        endpoint.health_status = HealthStatus.UNREACHABLE
        endpoint.last_health_at = datetime.now(UTC)
        await self._db.flush()
        return HealthStatus.UNREACHABLE

    async def get_healthy_endpoints(
        self,
        model_id: str,
        workspace_id: uuid.UUID | None = None,
    ) -> list[InferenceEndpointModel]:
        endpoints = await self.list_endpoints(workspace_id)
        return [e for e in endpoints if e.model_id == model_id and e.health_status == HealthStatus.HEALTHY]

    async def invoke(
        self,
        endpoint_id: uuid.UUID,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        endpoint = await self.get_endpoint(endpoint_id)
        if endpoint is None:
            raise ValueError(f"Endpoint {endpoint_id} not found")
        if endpoint.health_status != HealthStatus.HEALTHY:
            raise RuntimeError(f"Endpoint {endpoint_id} is {endpoint.health_status}")
        return await self._backend.invoke(endpoint.url, request)

    async def start_health_polling(self, interval: float = 30.0) -> None:
        self._poll_interval = interval
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop_health_polling(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task

    async def _poll_loop(self) -> None:
        while True:
            try:
                endpoints = await self.list_endpoints()
                for endpoint in endpoints:
                    await self.check_health(endpoint.id)
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Health poll error")
                await asyncio.sleep(self._poll_interval)
