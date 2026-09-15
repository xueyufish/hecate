"""Tests for candidate review — approve publishes, reject retains, run concludes."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from hecate.models.prompt import PromptVersionModel
from hecate.models.prompt_optimization import PromptOptimizationCandidateModel
from hecate.ops.prompt_optimization.review import CandidateReviewError, CandidateReviewService
from tests.test_services.test_prompt_optimization.helpers import (
    make_run_row,
    seed_agent,
    seed_dataset_version,
    seed_prompt,
    ws_id,
)


async def _seed_pending(db_session, default_workspace) -> tuple:
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, "Baseline {{q}}.", ["q"])
    agent = seed_agent(db_session, ws)
    _, version = seed_dataset_version(db_session, ws, [])
    run = make_run_row(prompt, agent, version, ws, status="awaiting_review")
    db_session.add(run)
    await db_session.flush()
    candidate = PromptOptimizationCandidateModel(
        run_id=run.id,
        round_no=1,
        template="IMPROVED {{q}} — be precise.",
        status="pending_review",
        gate_report={
            "accepted": True,
            "primary_metric": "stub",
            "baseline_scores": {"stub": 0.4},
            "candidate_scores": {"stub": 0.9},
            "checks": [],
        },
        workspace_id=ws,
    )
    db_session.add(candidate)
    await db_session.flush()
    return prompt, run, candidate


async def test_approve_publishes_new_version_with_provenance(db_session, default_workspace, test_user_id) -> None:
    prompt, run, candidate = await _seed_pending(db_session, default_workspace)
    service = CandidateReviewService(db_session)

    decided, version = await service.approve(candidate.id, ws_id(default_workspace), decided_by=test_user_id)

    assert decided.status == "published"
    assert decided.decided_by == test_user_id
    assert decided.decided_at is not None
    assert version.version == 2
    assert version.template == "IMPROVED {{q}} — be precise."
    assert version.labels == []
    assert version.metadata_["source"] == "prompt_optimization"
    assert version.metadata_["run_id"] == str(run.id)
    assert version.metadata_["candidate_id"] == str(candidate.id)
    assert "run" in version.commit_message and "+0.500" in version.commit_message
    await db_session.refresh(prompt)
    assert prompt.current_version == 2
    # Run concludes: the only candidate reached a terminal decision.
    await db_session.refresh(run)
    assert run.status == "concluded"


async def test_published_version_behaves_like_any_version(db_session, default_workspace, test_user_id) -> None:
    prompt, run, candidate = await _seed_pending(db_session, default_workspace)
    service = CandidateReviewService(db_session)
    _, version = await service.approve(candidate.id, ws_id(default_workspace), decided_by=test_user_id)

    rows = (
        (await db_session.execute(select(PromptVersionModel).where(PromptVersionModel.prompt_id == prompt.id)))
        .scalars()
        .all()
    )
    assert {v.version for v in rows} == {1, 2}
    assert version.labels == []  # no label auto-applied


async def test_approve_non_pending_candidate_conflicts(db_session, default_workspace, test_user_id) -> None:
    prompt, run, candidate = await _seed_pending(db_session, default_workspace)
    candidate.status = "gate_rejected"
    await db_session.flush()

    with pytest.raises(CandidateReviewError):
        await CandidateReviewService(db_session).approve(
            candidate.id, ws_id(default_workspace), decided_by=test_user_id
        )


async def test_reject_requires_reason_and_retains_candidate(db_session, default_workspace, test_user_id) -> None:
    prompt, run, candidate = await _seed_pending(db_session, default_workspace)
    service = CandidateReviewService(db_session)

    rejected = await service.reject(candidate.id, ws_id(default_workspace), "regressed tone", decided_by=test_user_id)

    assert rejected.status == "rejected"
    assert rejected.rejection_reason == "regressed tone"
    assert rejected.decided_at is not None
    await db_session.refresh(run)
    assert run.status == "concluded"
    # Nothing is deleted: the candidate stays queryable.
    assert await service.get_candidate(candidate.id, ws_id(default_workspace)) is not None


async def test_run_stays_awaiting_review_until_all_decided(db_session, default_workspace, test_user_id) -> None:
    prompt, run, candidate = await _seed_pending(db_session, default_workspace)
    ws = ws_id(default_workspace)
    second = PromptOptimizationCandidateModel(
        run_id=run.id,
        round_no=2,
        template="Second {{q}} candidate.",
        status="pending_review",
        workspace_id=ws,
    )
    db_session.add(second)
    await db_session.flush()

    service = CandidateReviewService(db_session)
    await service.reject(candidate.id, ws, "first is worse", decided_by=test_user_id)
    await db_session.refresh(run)
    assert run.status == "awaiting_review"

    await service.reject(second.id, ws, "second too", decided_by=test_user_id)
    await db_session.refresh(run)
    assert run.status == "concluded"
