"""Prompt analytics service — version diff, metrics aggregation, AI summaries.

Provides business logic for prompt version comparison and analytics:
- compute_diff: line-level diff between two prompt versions
- get_version_analytics: trace-derived metrics for a specific version
- compare_versions: side-by-side metrics for two versions
- generate_change_summary: AI-assisted change description
"""

from __future__ import annotations

import difflib
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.prompt import PromptVersionModel
from hecate.models.trace import TraceModel

logger = logging.getLogger(__name__)


class PromptAnalyticsService:
    """Service for prompt version analytics and diff computation."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def compute_diff(
        self,
        prompt_id: uuid.UUID,
        from_version: int,
        to_version: int,
    ) -> dict:
        """Compute a line-level diff between two prompt versions.

        Args:
            prompt_id: UUID of the prompt.
            from_version: Source version number.
            to_version: Target version number.

        Returns:
            Structured diff result with entries, counts, and metadata.

        Raises:
            ValueError: If a version is not found.
        """
        from_v = await self._get_version(prompt_id, from_version)
        to_v = await self._get_version(prompt_id, to_version)

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
        days: int = 7,
    ) -> dict:
        """Aggregate trace-derived metrics for a specific prompt version.

        Args:
            prompt_id: UUID of the prompt.
            version: Version number to analyze.
            days: Number of days to look back.

        Returns:
            Aggregated metrics dict.
        """
        cutoff = datetime.now(UTC) - timedelta(days=days)
        prompt_id_str = str(prompt_id)

        stmt = select(
            func.count().label("total_calls"),
            func.avg(func.extract("epoch", TraceModel.end_time - TraceModel.start_time) * 1000).label("avg_latency_ms"),
            func.coalesce(
                func.sum(TraceModel.usage["total_tokens"].as_integer()),
                0,
            ).label("total_tokens"),
            func.count().filter(TraceModel.status == "error").label("error_count"),
        ).where(
            TraceModel.metadata_["prompt_id"].as_string() == prompt_id_str,
            TraceModel.metadata_["prompt_version"].as_string() == str(version),
            TraceModel.start_time >= cutoff,
        )

        result = await self.db.execute(stmt)
        row = result.one_or_none()

        if row is None or row.total_calls == 0:
            return {
                "version": version,
                "total_calls": 0,
                "avg_latency_ms": 0.0,
                "total_tokens": 0,
                "error_rate": 0.0,
                "total_cost": 0.0,
                "daily_breakdown": [],
            }

        total_calls = row.total_calls or 0
        avg_latency = round(float(row.avg_latency_ms or 0), 2)
        total_tokens = int(row.total_tokens or 0)
        error_count = row.error_count or 0
        error_rate = round(error_count / total_calls, 4) if total_calls > 0 else 0.0

        daily_stmt = (
            select(
                func.date(TraceModel.start_time).label("day"),
                func.count().label("calls"),
            )
            .where(
                TraceModel.metadata_.op("->>")("prompt_id") == prompt_id_str,
                TraceModel.metadata_.op("->>")("prompt_version") == str(version),
                TraceModel.start_time >= cutoff,
            )
            .group_by(func.date(TraceModel.start_time))
            .order_by(func.date(TraceModel.start_time))
        )

        daily_result = await self.db.execute(daily_stmt)
        daily_breakdown = [{"day": str(row.day), "calls": row.calls} for row in daily_result]

        return {
            "version": version,
            "total_calls": total_calls,
            "avg_latency_ms": avg_latency,
            "total_tokens": total_tokens,
            "error_rate": error_rate,
            "total_cost": 0.0,
            "daily_breakdown": daily_breakdown,
        }

    async def compare_versions(
        self,
        prompt_id: uuid.UUID,
        from_version: int,
        to_version: int,
        days: int = 7,
    ) -> dict:
        """Compare analytics for two prompt versions side by side.

        Args:
            prompt_id: UUID of the prompt.
            from_version: Baseline version number.
            to_version: Candidate version number.
            days: Number of days to look back.

        Returns:
            Side-by-side comparison with deltas.
        """
        from_analytics = await self.get_version_analytics(prompt_id, from_version, days)
        to_analytics = await self.get_version_analytics(prompt_id, to_version, days)

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
    ) -> dict:
        """Generate an AI-assisted change summary for a prompt version.

        Args:
            prompt_id: UUID of the prompt.
            version: Version number to summarize.

        Returns:
            Dict with summary text and metadata.
        """
        if version <= 1:
            return {
                "version": version,
                "summary": "This is the initial version of the prompt. No changes to summarize.",
                "is_initial": True,
            }

        diff = await self.compute_diff(prompt_id, version - 1, version)

        if diff["added_lines"] == 0 and diff["removed_lines"] == 0:
            return {
                "version": version,
                "summary": "No template changes between this version and the previous one.",
                "is_initial": False,
            }

        try:
            from hecate.services.llm.service import LLMService

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

    async def _get_version(
        self,
        prompt_id: uuid.UUID,
        version: int,
    ) -> PromptVersionModel:
        """Fetch a specific prompt version or raise ValueError."""
        stmt = select(PromptVersionModel).where(
            PromptVersionModel.prompt_id == prompt_id,
            PromptVersionModel.version == version,
            ~PromptVersionModel.deleted,
        )
        result = await self.db.execute(stmt)
        v = result.scalar_one_or_none()
        if v is None:
            raise ValueError(f"Version {version} not found for prompt {prompt_id}")
        return v
