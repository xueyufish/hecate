"""Shared seeding helpers for prompt-optimization tests."""

from __future__ import annotations

import hashlib
import uuid

from hecate.models.agent import AgentModel
from hecate.models.evaluation import EvaluationDatasetModel, EvaluationDatasetVersionModel
from hecate.models.prompt import PromptModel, PromptVersionModel
from hecate.models.prompt_optimization import PromptOptimizationRunModel

ZERO_WS = uuid.UUID("00000000-0000-0000-0000-000000000000")


def ws_id(workspace) -> uuid.UUID:
    """Workspace id from a WorkspaceModel row (conftest default_workspace)."""
    return workspace.id


def seed_prompt(db, workspace_id: uuid.UUID, template: str, variables: list[str]) -> PromptModel:
    prompt = PromptModel(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name=f"opt-prompt-{uuid.uuid4().hex[:8]}",
        current_version=1,
    )
    db.add(prompt)
    db.add(
        PromptVersionModel(
            prompt_id=prompt.id,
            version=1,
            template=template,
            variables=variables,
            labels=[],
            workspace_id=workspace_id,
        )
    )
    return prompt


def seed_agent(db, workspace_id: uuid.UUID) -> AgentModel:
    agent = AgentModel(
        id=uuid.uuid4(),
        name=f"opt-agent-{uuid.uuid4().hex[:8]}",
        persona="Production persona.",
        model_config_db={"model": "gpt-4o"},
        tools=[],
        knowledge_base_ids=[],
        workspace_id=workspace_id,
    )
    db.add(agent)
    return agent


def seed_dataset_version(
    db,
    workspace_id: uuid.UUID,
    items: list[dict],
) -> tuple[EvaluationDatasetModel, EvaluationDatasetVersionModel]:
    dataset = EvaluationDatasetModel(
        id=uuid.uuid4(),
        name=f"opt-ds-{uuid.uuid4().hex[:8]}",
        workspace_id=workspace_id,
    )
    db.add(dataset)
    blob = repr(items).encode()
    version = EvaluationDatasetVersionModel(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        name=f"v-{uuid.uuid4().hex[:8]}",
        items=items,
        content_hash=hashlib.sha256(blob).hexdigest(),
        workspace_id=workspace_id,
    )
    db.add(version)
    return dataset, version


def make_run_row(
    prompt: PromptModel,
    agent: AgentModel,
    version: EvaluationDatasetVersionModel,
    workspace_id: uuid.UUID,
    *,
    primary_metric: str = "stub",
    max_rounds: int = 2,
    rollout_item_limit: int = 100,
    split_ratio: float = 0.5,
    status: str = "running",
) -> PromptOptimizationRunModel:
    return PromptOptimizationRunModel(
        prompt_id=prompt.id,
        base_version=1,
        agent_id=agent.id,
        dataset_id=version.dataset_id,
        dataset_version_id=version.id,
        dataset_version_hash=version.content_hash,
        split_ratio=split_ratio,
        evaluator_configs=["stub"],
        primary_metric=primary_metric,
        min_improvement=0.02,
        max_regression=0.05,
        budget_preset="light",
        max_rounds=max_rounds,
        mutation_call_limit=10,
        rollout_item_limit=rollout_item_limit,
        strategy="reflective_mutation",
        reflection_model="test-model",
        status=status,
        workspace_id=workspace_id,
    )


def dataset_items(n: int) -> list[dict]:
    """Frozen dataset-version item entries with deterministic expected answers."""
    return [
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"item-{i}")),
            "query": f"question {i}",
            "expected_answer": f"expected-{i}",
            "context": [],
            "tags": [],
            "metadata": {},
            "known_bad": False,
        }
        for i in range(n)
    ]
