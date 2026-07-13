"""Plugin ORM model — persists plugin state and configuration."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class PluginModel(BaseModel):
    __tablename__ = "plugins"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="0.0.0")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="installed")
    entry: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    manifest_: Mapped[dict] = mapped_column("manifest", JSON, nullable=False, default=dict)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True
    )
