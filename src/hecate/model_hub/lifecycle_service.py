"""Model lifecycle service — promotion, approval, deprecation, rollback.

Manages staging channels (dev/staging/prod) with approval workflows,
deprecation scheduling, and version rollback.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.model_deployment import ModelDeploymentModel

logger = logging.getLogger(__name__)

_DEFAULT_WORKSPACE = uuid.UUID("00000000-0000-0000-0000-000000000000")


class LifecycleService:
    """Service for model lifecycle management.

    Args:
        db: Async SQLAlchemy session.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def promote(
        self,
        model_id: str,
        from_channel: str,
        to_channel: str,
        workspace_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Promote a model from one channel to the next.

        Args:
            model_id: Model identifier.
            from_channel: Source channel (dev, staging).
            to_channel: Target channel (staging, prod).
            workspace_id: Workspace scope.

        Returns:
            Dict with promotion status.
        """
        ws_id = workspace_id or _DEFAULT_WORKSPACE

        # Verify source deployment exists and is approved
        source = await self._get_deployment(model_id, from_channel, ws_id)
        if source is None:
            msg = f"No deployment found for {model_id} in {from_channel}"
            raise ValueError(msg)
        if source.approval_status != "approved":
            msg = f"Source deployment is not approved (status: {source.approval_status})"
            raise ValueError(msg)

        # Check if target deployment already exists
        existing = await self._get_deployment(model_id, to_channel, ws_id)
        if existing is not None:
            msg = f"Deployment already exists for {model_id} in {to_channel}"
            raise ValueError(msg)

        # Create pending deployment in target channel
        deployment = ModelDeploymentModel(
            model_id=model_id,
            channel=to_channel,
            version=source.version,
            deployment_config=source.deployment_config,
            approval_status="pending",
            workspace_id=ws_id,
        )
        self._db.add(deployment)
        await self._db.flush()

        logger.info("Promoted %s from %s to %s (pending approval)", model_id, from_channel, to_channel)
        return {
            "deployment_id": str(deployment.id),
            "model_id": model_id,
            "from_channel": from_channel,
            "to_channel": to_channel,
            "approval_status": "pending",
        }

    async def approve(
        self,
        deployment_id: uuid.UUID,
        approver_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Approve a pending deployment.

        Args:
            deployment_id: Deployment UUID.
            approver_id: User UUID of approver.

        Returns:
            Dict with approval status.
        """
        deployment = await self._get_deployment_by_id(deployment_id)
        if deployment is None:
            msg = f"Deployment {deployment_id} not found"
            raise ValueError(msg)
        if deployment.approval_status != "pending":
            msg = f"Deployment is not pending (status: {deployment.approval_status})"
            raise ValueError(msg)

        deployment.approval_status = "approved"
        deployment.approved_by = approver_id
        deployment.approved_at = datetime.now(UTC)
        await self._db.flush()

        logger.info("Approved deployment %s by %s", deployment_id, approver_id)
        return {"deployment_id": str(deployment_id), "approval_status": "approved"}

    async def reject(
        self,
        deployment_id: uuid.UUID,
        reason: str = "",
    ) -> dict[str, Any]:
        """Reject a pending deployment.

        Args:
            deployment_id: Deployment UUID.
            reason: Rejection reason.

        Returns:
            Dict with rejection status.
        """
        deployment = await self._get_deployment_by_id(deployment_id)
        if deployment is None:
            msg = f"Deployment {deployment_id} not found"
            raise ValueError(msg)

        deployment.approval_status = "rejected"
        await self._db.flush()

        logger.info("Rejected deployment %s: %s", deployment_id, reason)
        return {"deployment_id": str(deployment_id), "approval_status": "rejected", "reason": reason}

    async def deprecate(
        self,
        model_id: str,
        sunset_at: datetime,
        workspace_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Schedule model deprecation with sunset date.

        Args:
            model_id: Model identifier.
            sunset_at: When to disable the model.
            workspace_id: Workspace scope.

        Returns:
            Dict with deprecation status.
        """
        ws_id = workspace_id or _DEFAULT_WORKSPACE

        # Find prod deployment
        deployment = await self._get_deployment(model_id, "prod", ws_id)
        if deployment is None:
            msg = f"No prod deployment found for {model_id}"
            raise ValueError(msg)

        deployment.deprecated_at = datetime.now(UTC)
        deployment.sunset_at = sunset_at
        await self._db.flush()

        logger.info("Deprecated %s, sunset at %s", model_id, sunset_at)
        return {
            "model_id": model_id,
            "deprecated_at": deployment.deprecated_at.isoformat(),
            "sunset_at": sunset_at.isoformat(),
        }

    async def cancel_deprecation(
        self,
        model_id: str,
        workspace_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Cancel model deprecation.

        Args:
            model_id: Model identifier.
            workspace_id: Workspace scope.

        Returns:
            Dict with cancellation status.
        """
        ws_id = workspace_id or _DEFAULT_WORKSPACE

        deployment = await self._get_deployment(model_id, "prod", ws_id)
        if deployment is None:
            msg = f"No prod deployment found for {model_id}"
            raise ValueError(msg)

        deployment.deprecated_at = None
        deployment.sunset_at = None
        await self._db.flush()

        logger.info("Cancelled deprecation for %s", model_id)
        return {"model_id": model_id, "status": "deprecation_cancelled"}

    async def rollback(
        self,
        model_id: str,
        to_version: str,
        workspace_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Rollback model to a previous version.

        Args:
            model_id: Model identifier.
            to_version: Version to rollback to.
            workspace_id: Workspace scope.

        Returns:
            Dict with rollback status.
        """
        ws_id = workspace_id or _DEFAULT_WORKSPACE

        deployment = await self._get_deployment(model_id, "prod", ws_id)
        if deployment is None:
            msg = f"No prod deployment found for {model_id}"
            raise ValueError(msg)

        old_version = deployment.version
        deployment.version = to_version
        deployment.approval_status = "approved"
        deployment.approved_at = datetime.now(UTC)
        deployment.deprecated_at = None
        deployment.sunset_at = None
        await self._db.flush()

        logger.info("Rolled back %s from %s to %s", model_id, old_version, to_version)
        return {
            "model_id": model_id,
            "from_version": old_version,
            "to_version": to_version,
            "status": "rolled_back",
        }

    async def list_deployments(
        self,
        channel: str | None = None,
        approval_status: str | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        """List all model deployments with optional filters.

        Args:
            channel: Filter by channel.
            approval_status: Filter by approval status.
            workspace_id: Workspace scope.

        Returns:
            List of deployment entries.
        """
        ws_id = workspace_id or _DEFAULT_WORKSPACE
        conditions = [
            ModelDeploymentModel.workspace_id == ws_id,
            ModelDeploymentModel.deleted.is_(False),
        ]
        if channel:
            conditions.append(ModelDeploymentModel.channel == channel)
        if approval_status:
            conditions.append(ModelDeploymentModel.approval_status == approval_status)

        stmt = (
            select(ModelDeploymentModel)
            .where(*conditions)
            .order_by(ModelDeploymentModel.model_id, ModelDeploymentModel.channel)
        )
        result = await self._db.execute(stmt)
        deployments = result.scalars().all()

        return [
            {
                "id": str(d.id),
                "model_id": d.model_id,
                "channel": d.channel,
                "version": d.version,
                "approval_status": d.approval_status,
                "approved_by": str(d.approved_by) if d.approved_by else None,
                "approved_at": d.approved_at.isoformat() if d.approved_at else None,
                "deprecated_at": d.deprecated_at.isoformat() if d.deprecated_at else None,
                "sunset_at": d.sunset_at.isoformat() if d.sunset_at else None,
                "is_enabled": d.is_enabled,
            }
            for d in deployments
        ]

    async def _get_deployment(
        self,
        model_id: str,
        channel: str,
        workspace_id: uuid.UUID,
    ) -> ModelDeploymentModel | None:
        """Get deployment by model_id and channel."""
        stmt = select(ModelDeploymentModel).where(
            ModelDeploymentModel.model_id == model_id,
            ModelDeploymentModel.channel == channel,
            ModelDeploymentModel.workspace_id == workspace_id,
            ModelDeploymentModel.deleted.is_(False),
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_deployment_by_id(
        self,
        deployment_id: uuid.UUID,
    ) -> ModelDeploymentModel | None:
        """Get deployment by ID."""
        stmt = select(ModelDeploymentModel).where(
            ModelDeploymentModel.id == deployment_id,
            ModelDeploymentModel.deleted.is_(False),
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()
