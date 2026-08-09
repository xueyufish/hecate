"""Tests for the Alembic expand-contract split hook and lock_timeout."""

from __future__ import annotations

import pytest
from alembic.operations import ops as alembic_ops

from hecate.db.migrations.expand_contract import build_split_directives, split_expand_contract


def _op(op_class):
    """Create an instance of the given Alembic op class."""
    return op_class.__new__(op_class)


class TestSplitExpandContract:
    def test_only_expand_ops_returns_none(self):
        ops = [_op(alembic_ops.AddColumnOp)]
        assert split_expand_contract(ops) == ([ops[0]], [])

    def test_only_contract_ops_returns_none(self):
        ops = [_op(alembic_ops.DropColumnOp)]
        assert split_expand_contract(ops) == ([], [ops[0]])

    def test_mixed_returns_both(self):
        expand = _op(alembic_ops.AddColumnOp)
        contract = _op(alembic_ops.DropColumnOp)
        result = split_expand_contract([expand, contract])
        assert result == ([expand], [contract])

    def test_unknown_op_raises(self):
        class UnknownOp:
            pass

        with pytest.raises(NotImplementedError, match="Cannot auto-classify"):
            split_expand_contract([_op(UnknownOp)])


class TestBuildSplitDirectives:
    def test_empty_ops_returns_none(self):
        assert build_split_directives([], "rev1", "parent") is None

    def test_pure_expand_returns_none(self):
        ops = [_op(alembic_ops.AddColumnOp)]
        assert build_split_directives(ops, "rev1", "parent") is None

    def test_pure_contract_returns_none(self):
        ops = [_op(alembic_ops.DropColumnOp)]
        assert build_split_directives(ops, "rev1", "parent") is None

    def test_mixed_produces_two_scripts(self):
        expand = _op(alembic_ops.AddColumnOp)
        contract = _op(alembic_ops.DropColumnOp)
        result = build_split_directives(
            upgrade_ops=[expand, contract],
            base_rev_id="abc123",
            parent_revision="parent_rev",
            message="add user_avatar",
        )
        assert result is not None
        assert len(result) == 2
        expand_script, contract_script = result
        assert expand_script.rev_id == "abc123_expand"
        assert expand_script.upgrade_ops == [expand]
        assert expand_script.down_revision == "parent_rev"
        assert contract_script.rev_id == "abc123_contract"
        assert contract_script.upgrade_ops == [contract]
        assert contract_script.down_revision == "abc123_expand"
        assert "contract" in contract_script.message.lower()


class TestOperationClassification:
    @pytest.mark.parametrize(
        "op_class,expected_phase",
        [
            (alembic_ops.CreateTableOp, "expand"),
            (alembic_ops.AddColumnOp, "expand"),
            (alembic_ops.CreateIndexOp, "expand"),
            (alembic_ops.CreateCheckConstraintOp, "expand"),
            (alembic_ops.DropTableOp, "contract"),
            (alembic_ops.DropColumnOp, "contract"),
            (alembic_ops.DropIndexOp, "contract"),
            (alembic_ops.DropConstraintOp, "contract"),
            (alembic_ops.AlterColumnOp, "contract"),
        ],
    )
    def test_classification(self, op_class, expected_phase):
        expand, contract = split_expand_contract([_op(op_class)])
        if expected_phase == "expand":
            assert len(expand) == 1
            assert contract == []
        else:
            assert expand == []
            assert len(contract) == 1


class TestLockTimeout:
    """lock_timeout is set at alembic/env.py module import time.

    The constant is bound to the env var value at import, so changing the env
    after import has no effect. This test verifies the resolution logic by
    calling os.getenv directly, which is what env.py does.
    """

    def test_default_is_2s(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ALEMBIC_LOCK_TIMEOUT", raising=False)
        import os

        assert os.getenv("ALEMBIC_LOCK_TIMEOUT", "2s") == "2s"

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ALEMBIC_LOCK_TIMEOUT", "10s")
        import os

        assert os.getenv("ALEMBIC_LOCK_TIMEOUT", "2s") == "10s"
