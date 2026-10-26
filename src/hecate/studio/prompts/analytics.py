"""Prompt analytics service — version diff, metrics aggregation, AI summaries.

Provides business logic for prompt version comparison and analytics:
- compute_diff: line-level diff between two prompt versions
- get_version_analytics: trace-derived metrics for a specific version
- compare_versions: side-by-side metrics for two versions
- generate_change_summary: AI-assisted change description

Every operation is workspace-scoped: versions are resolved through the
parent prompt's workspace binding before any content is read, and trace
metrics count only traces whose Session/Agent association resolves into
the caller's workspace. ``TraceModel`` carries no workspace column — the
``prompt_id`` / ``prompt_version`` metadata is a metric selector, never a
tenant authorization: a trace participates in aggregates only when its
session row (or, for session-less traces, its agent row) is live and
workspace-consistent. Orphaned traces, foreign-workspace associations,
soft-deleted associations, and session/agent pairs that disagree on the
workspace are all excluded. Aggregation happens in Python from the scoped
row set so the arithmetic is identical on SQLite and PostgreSQL.
"""

from __future__ import annotations

import difflib
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import String, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.prompt import PromptModel, PromptVersionModel
from hecate.models.session import SessionModel
from hecate.models.trace import TraceModel

logger = logging.getLogger(__name__)

VERSION_NOT_FOUND = "Prompt or version not found"


class PromptAnalyticsService:
    """Service for prompt version analytics and diff computation."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def compute_diff(
        self,
        prompt_id: uuid.UUID,
        from_version: int,
        to_version: int,
        workspace_id: uuid.UUID,
    ) -> dict:
        """Compute a line-level diff between two prompt versions.

        Args:
            prompt_id: UUID of the prompt.
            from_version: Source version number.
            to_version: Target version number.
            workspace_id: Workspace the prompt must belong to.

        Returns:
            Structured diff result with entries, counts, and metadata.

        Raises:
            LookupError: If a version is not visible in the workspace.
        """
        from_v = await self._require_version(prompt_id, from_version, workspace_id)
        to_v = await self._require_version(prompt_id, to_version, workspace_id)

        from_lines = (from_v.template or "").splitlines(keepends=True)
        to_lines = (to_v.template or "").splitlines(keepends=True)

        diff_entries: list[dict] = []
        added = 0
        removed = 0

        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, from_lines, to_lines).get_opcodes():
            if tag == "equal":
                for k, line in enumerate(from_lines[i1:i2]):
                    diff_entries.append(
                        {
                            "type": "context",
                            "from_line": i1 + k + 1,
                            "to_line": j1 + k + 1,
                            "content": line.rstrip("\n"),
                        }
                    )
            elif tag == "replace":
                for k, line in enumerate(from_lines[i1:i2]):
                    diff_entries.append(
                        {
                            "type": "removed",
                            "from_line": i1 + k + 1,
                            "to_line": None,
                            "content": line.rstrip("\n"),
                        }
                    )
                    removed += 1
                for k, line in enumerate(to_lines[j1:j2]):
                    diff_entries.append(
                        {
                            "type": "added",
                            "from_line": None,
                            "to_line": j1 + k + 1,
                            "content": line.rstrip("\n"),
                        }
                    )
                    added += 1
            elif tag == "insert":
                for k, line in enumerate(to_lines[j1:j2]):
                    diff_entries.append(
                        {
                            "type": "added",
                            "from_line": None,
                            "to_line": j1 + k + 1,
                            "content": line.rstrip("\n"),
                        }
                    )
                    added += 1
            elif tag == "delete":
                for k, line in enumerate(from_lines[i1:i2]):
                    diff_entries.append(
                        {
                            "type": "removed",
                            "from_line": i1 + k + 1,
                            "to_line": None,
                            "content": line.rstrip("\n"),
                        }
                    )
                    removed += 1

        from_tokens = len(from_v.template.split()) if from_v.template else 0
        to_tokens = len(to_v.template.split()) if to_v.template else 0

        return {
            "from_version": from_version,
            "to_version": to_version,
            "from_commit_message": from_v.commit_message,
            "to_commit_message": to_v.commit_message,
            "added_lines": added,
            "removed_lines": removed,
            "token_delta": to_tokens - from_tokens,
            "diff_entries": diff_entries,
        }

    async def get_version_analytics(
        self,
        prompt_id: uuid.UUID,
        version: int,
        workspace_id: uuid.UUID,
        days: int = 7,
    ) -> dict:
        """Aggregate trace-derived metrics for a specific prompt version.

        The version is validated first — a missing or workspace-inconsistent
        version is a 404, never a "zero calls" success. Metrics count only
        workspace-visible traces whose metadata selects this prompt version.

        Args:
            prompt_id: UUID of the prompt.
            version: Version number to analyze.
            workspace_id: Workspace the prompt must belong to.
            days: Number of days to look back.

        Returns:
            Aggregated metrics dict.

        Raises:
            LookupError: If the version is not visible in the workspace.
        """
        await self._require_version(prompt_id, version, workspace_id)

        cutoff = datetime.now(UTC) - timedelta(days=days)
        rows = await self._scoped_trace_rows(prompt_id, version, workspace_id, cutoff)

        return self._aggregate(version, rows)

    async def compare_versions(
        self,
        prompt_id: uuid.UUID,
        from_version: int,
        to_version: int,
        workspace_id: uuid.UUID,
        days: int = 7,
    ) -> dict:
        """Compare analytics for two prompt versions side by side.

        Both versions are validated before any statistics run.

        Args:
            prompt_id: UUID of the prompt.
            from_version: Baseline version number.
            to_version: Candidate version number.
            workspace_id: Workspace the prompt must belong to.
            days: Number of days to look back.

        Returns:
            Side-by-side comparison with deltas.

        Raises:
            LookupError: If either version is not visible in the workspace.
        """
        await self._require_version(prompt_id, from_version, workspace_id)
        await self._require_version(prompt_id, to_version, workspace_id)

        from_analytics = await self.get_version_analytics(prompt_id, from_version, workspace_id, days)
        to_analytics = await self.get_version_analytics(prompt_id, to_version, workspace_id, days)

        deltas: dict[str, float] = {}
        for key in ("total_calls", "avg_latency_ms", "total_tokens", "error_rate", "total_cost"):
            from_val = float(from_analytics.get(key, 0))
            to_val = float(to_analytics.get(key, 0))
            deltas[key] = round(to_val - from_val, 4)

        return {
            "from_version": from_version,
            "to_version": to_version,
            "from_analytics": from_analytics,
            "to_analytics": to_analytics,
            "deltas": deltas,
        }

    async def generate_change_summary(
        self,
        prompt_id: uuid.UUID,
        version: int,
        workspace_id: uuid.UUID,
    ) -> dict:
        """Generate an AI-assisted change summary for a prompt version.

        Workspace binding is verified before every shortcut — the initial-
        version and no-change responses are only reachable for a version
        that is visible in the workspace.

        Args:
            prompt_id: UUID of the prompt.
            version: Version number to summarize.
            workspace_id: Workspace the prompt must belong to.

        Returns:
            Dict with summary text and metadata.

        Raises:
            LookupError: If the version (or, for diffs, its predecessor)
                is not visible in the workspace.
        """
        await self._require_version(prompt_id, version, workspace_id)

        if version <= 1:
            return {
                "version": version,
                "summary": "This is the initial version of the prompt. No changes to summarize.",
                "is_initial": True,
            }

        diff = await self.compute_diff(prompt_id, version - 1, version, workspace_id)

        if diff["added_lines"] == 0 and diff["removed_lines"] == 0:
            return {
                "version": version,
                "summary": "No template changes between this version and the previous one.",
                "is_initial": False,
            }

        try:
            from hecate_llm.service import LLMService

            diff_text = "\n".join(f"{e['type'].upper()}: {e['content']}" for e in diff["diff_entries"])
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a prompt engineering assistant. Summarize the changes "
                        "between two prompt versions in 2-3 sentences. Focus on what "
                        "changed (instructions, tone, variables, structure) and why it "
                        "might matter. Be concise and specific."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Diff between version {version - 1} and version {version}:\n{diff_text}",
                },
            ]
            llm = LLMService()
            response = await llm.chat(messages=messages, model="gpt-4o-mini")
            summary = (response.content or "").strip()
        except Exception as e:
            logger.warning("Failed to generate change summary: %s", e)
            summary = (
                f"Version {version} has {diff['added_lines']} added lines "
                f"and {diff['removed_lines']} removed lines compared to version {version - 1}."
            )

        return {
            "version": version,
            "summary": summary,
            "is_initial": False,
            "diff_stats": {
                "added_lines": diff["added_lines"],
                "removed_lines": diff["removed_lines"],
                "token_delta": diff["token_delta"],
            },
        }

    # --- internal helpers --------------------------------------------------

    async def _require_version(
        self,
        prompt_id: uuid.UUID,
        version: int,
        workspace_id: uuid.UUID,
    ) -> PromptVersionModel:
        """Fetch a workspace-consistent version or raise LookupError.

        The version must belong to the target prompt, share the parent
        prompt's workspace, and have a live parent in that workspace.
        ``populate_existing`` defeats identity-map staleness from earlier
        prechecks on the same session.
        """
        stmt = (
            select(PromptVersionModel)
            .join(PromptModel, PromptModel.id == PromptVersionModel.prompt_id)
            .where(
                PromptVersionModel.prompt_id == prompt_id,
                PromptVersionModel.version == version,
                PromptVersionModel.workspace_id == workspace_id,
                ~PromptVersionModel.deleted,
                PromptModel.workspace_id == workspace_id,
                ~PromptModel.deleted,
            )
            .execution_options(populate_existing=True)
        )
        result = await self.db.execute(stmt)
        v = result.scalar_one_or_none()
        if v is None:
            raise LookupError(VERSION_NOT_FOUND)
        return v

    def _trace_visibility(self, workspace_id: uuid.UUID):
        """Trace participation predicate from Session/Agent row association.

        A trace participates when its session row resolves into the
        workspace and its agent (if any) agrees, or — for session-less
        traces — its agent row resolves into the workspace. Orphans,
        foreign associations, soft-deleted associations, and session/agent
        pairs pointing at different workspaces are excluded.
        """
        session_in_ws = TraceModel.session_id.in_(
            select(SessionModel.id)
            .join(AgentModel, AgentModel.id == SessionModel.agent_id)
            .where(
                SessionModel.workspace_id == workspace_id,
                ~SessionModel.deleted,
                AgentModel.workspace_id == workspace_id,
                ~AgentModel.deleted,
            )
        )
        agent_in_ws = TraceModel.agent_id.in_(
            select(AgentModel.id).where(
                AgentModel.workspace_id == workspace_id,
                ~AgentModel.deleted,
            )
        )
        return or_(
            and_(session_in_ws, or_(TraceModel.agent_id.is_(None), agent_in_ws)),
            and_(TraceModel.session_id.is_(None), agent_in_ws),
        )

    async def _scoped_trace_rows(
        self,
        prompt_id: uuid.UUID,
        version: int,
        workspace_id: uuid.UUID,
        cutoff: datetime,
    ) -> list:
        """Load the raw trace rows feeding the aggregates.

        Metadata matching (``prompt_id`` / ``prompt_version``) selects the
        metric population; the Session/Agent predicate authorizes it. The
        time cutoff is applied in SQL before rows are materialized. The
        selected JSON marker is checked without integer coercion, so
        malformed metadata cannot alias a valid version or fail a cast.
        """
        stmt = select(
            TraceModel.start_time,
            TraceModel.end_time,
            TraceModel.usage,
            TraceModel.status,
            TraceModel.metadata_["prompt_version"].label("version_marker"),
        ).where(
            TraceModel.metadata_["prompt_id"].as_string() == str(prompt_id),
            # Text conversion handles numeric/string markers without a
            # PostgreSQL integer cast that malformed metadata can break.
            TraceModel.metadata_["prompt_version"].as_string().cast(String) == str(version),
            ~TraceModel.deleted,
            TraceModel.start_time >= cutoff,
            self._trace_visibility(workspace_id),
        )
        if self.db.get_bind().dialect.name == "sqlite":
            # SQLite's scalar JSON extraction turns true into integer 1;
            # preserve the original JSON type before decoding the value.
            stmt = stmt.where(func.json_type(TraceModel.metadata_, "$.prompt_version").in_(["integer", "text"]))
        result = await self.db.execute(stmt)
        rows = result.all()
        return [
            row
            for row in rows
            if (type(row.version_marker) is int and row.version_marker == version)
            or (isinstance(row.version_marker, str) and row.version_marker == str(version))
        ]

    @staticmethod
    def _aggregate(version: int, rows: list) -> dict:
        """Aggregate scoped trace rows into the analytics response.

        Kept as pure Python over the scoped row set so the arithmetic (and
        its dialect portability) is identical everywhere.
        """
        total_calls = len(rows)
        if total_calls == 0:
            return {
                "version": version,
                "total_calls": 0,
                "avg_latency_ms": 0.0,
                "total_tokens": 0,
                "error_rate": 0.0,
                "total_cost": 0.0,
                "daily_breakdown": [],
            }

        latencies = [(row.end_time - row.start_time).total_seconds() * 1000 for row in rows if row.end_time is not None]
        total_tokens = sum(int((row.usage or {}).get("total_tokens") or 0) for row in rows)
        error_count = sum(1 for row in rows if row.status == "error")

        daily: dict[str, int] = {}
        for row in rows:
            day = row.start_time.date().isoformat()
            daily[day] = daily.get(day, 0) + 1

        return {
            "version": version,
            "total_calls": total_calls,
            "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
            "total_tokens": total_tokens,
            "error_rate": round(error_count / total_calls, 4) if total_calls > 0 else 0.0,
            "total_cost": 0.0,
            "daily_breakdown": [{"day": day, "calls": calls} for day, calls in sorted(daily.items())],
        }
