"""Expand-contract migration utilities.

Extracted from alembic/env.py so they can be unit-tested without the
alembic CLI bootstrap (which fails outside an `alembic` invocation).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from alembic.operations import ops as alembic_ops

EXPAND_OPS: tuple[type, ...] = (
    alembic_ops.CreateTableOp,
    alembic_ops.AddColumnOp,
    alembic_ops.CreateIndexOp,
    alembic_ops.CreateCheckConstraintOp,
)
CONTRACT_OPS: tuple[type, ...] = (
    alembic_ops.DropTableOp,
    alembic_ops.DropColumnOp,
    alembic_ops.DropIndexOp,
    alembic_ops.DropConstraintOp,
    alembic_ops.AlterColumnOp,
)


def split_expand_contract(
    operations: list[Any],
) -> tuple[list[Any], list[Any]]:
    """Split a list of Alembic ops into (expand, contract) lists.

    Raises NotImplementedError if any op is not classifiable into either
    category — this prevents silent misclassification.
    """
    expand: list[Any] = []
    contract: list[Any] = []
    for op in operations:
        if isinstance(op, EXPAND_OPS):
            expand.append(op)
        elif isinstance(op, CONTRACT_OPS):
            contract.append(op)
        else:
            raise NotImplementedError(
                f"Cannot auto-classify operation {type(op).__name__} into expand/contract. "
                "Please split this operation manually into separate expand and contract revisions."
            )
    return expand, contract


def build_split_directives(
    upgrade_ops: list[Any],
    base_rev_id: str,
    parent_revision: str | None,
    message: str = "",
) -> list[Any] | None:
    """Produce one or two RevisionScript instances depending on op mix.

    Returns None if upgrade_ops is empty (nothing to split).
    Returns a single-element list if all ops are expand or all are contract
    (no split needed) — the caller uses the original directive.
    Returns a two-element list [expand_script, contract_script] if the ops
    mix expand + contract (split needed).
    """
    if not upgrade_ops:
        return None

    expand_ops, contract_ops = split_expand_contract(upgrade_ops)

    if not expand_ops or not contract_ops:
        return None

    expand_rev_id = f"{base_rev_id}_expand"
    contract_rev_id = f"{base_rev_id}_contract"

    def _script(rev_id: str, ops: list[Any], msg: str, down_rev: str | None) -> Any:
        return SimpleNamespace(
            rev_id=rev_id,
            imports=[],
            upgrade_ops=ops,
            downgrade_ops=list(reversed(ops)),
            down_revision=down_rev,
            message=msg,
            head_revision=None,
            branch_labels=None,
        )

    expand_script = _script(
        expand_rev_id,
        expand_ops,
        message,
        parent_revision,
    )
    contract_script = _script(
        contract_rev_id,
        contract_ops,
        f"{message} (contract phase)",
        expand_rev_id,
    )
    return [expand_script, contract_script]
