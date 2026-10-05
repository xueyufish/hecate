"""Evidence retention policy: parametrized backend suite + profile wiring.

Both backends (day-rolled JSONL files, SQL delegation to the host's own
durable database) run the same assertions through the
``EVIDENCE_IMPLEMENTATIONS`` factories — the runner-local analogue of the
``DURABLE_IMPLEMENTATIONS`` contract-suite pattern. Profile parsing,
fail-fast wiring, and the explicit no-policy default are covered at the
``load_profile`` level.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from conftest_runner import write_profile
from hecate_runner.evidence import (
    CATEGORY_CAPACITY,
    CATEGORY_UNWRITABLE,
    EvidenceCapacityError,
    EvidencePolicy,
    EvidenceStore,
    evidence_backend,
)
from hecate_runner.profile import ProfileError, load_profile

_T0 = 1_700_000_000.0
_DAY = 86_400.0


def _jsonl_factory(tmp_path: Path) -> Callable[[EvidencePolicy | None, Callable[[], float]], EvidenceStore]:
    def make(policy: EvidencePolicy | None, now: Callable[[], float]) -> EvidenceStore:
        return EvidenceStore(tmp_path / "evidence", policy=policy, now=now)

    return make


def _sql_factory(tmp_path: Path) -> Callable[[EvidencePolicy | None, Callable[[], float]], object]:
    def make(policy: EvidencePolicy | None, now: Callable[[], float]) -> object:
        from hecate_runner.evidence_sql import SqlEvidenceStore

        store = SqlEvidenceStore(f"sqlite:///{tmp_path / 'evidence.db'}", policy=policy, now=now)
        store.create_schema()
        return store

    return make


EVIDENCE_IMPLEMENTATIONS = {"jsonl": _jsonl_factory, "durable": _sql_factory}


@pytest.fixture(params=sorted(EVIDENCE_IMPLEMENTATIONS))
def store_factory(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Callable[[EvidencePolicy | None, Callable[[], float]], object]:
    return EVIDENCE_IMPLEMENTATIONS[request.param](tmp_path)


@pytest.fixture()
def shutdown_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # load_profile resolves the shutdown secret reference; fail-fast profile
    # tests that pass full validation need it present.
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown-token-value")


def _clock(state: dict) -> Callable[[], float]:
    return lambda: state["t"]


# --- shared backend semantics -------------------------------------------------------


def test_append_query_roundtrip_and_filters(store_factory) -> None:
    now = {"t": _T0}
    store = store_factory(None, _clock(now))
    store.append("execution", "app-reader", "run-1", "ok", {"tool": "query_inventory"})
    now["t"] += 5
    store.append("denial", "app-reader", "run-1", "denied", {"reason": "domain"})
    now["t"] += 5
    store.append("execution", "other", "run-2", "failed", {})

    assert [record.ref for record in store.query()] == ["run-2", "run-1", "run-1"]
    assert [record.ref for record in store.query(outcome="denied")] == ["run-1"]
    assert [record.ref for record in store.query(principal="other")] == ["run-2"]
    assert [record.outcome for record in store.query(kind="denial")] == ["denied"]
    assert store.query()[2].detail == {"tool": "query_inventory"}


def test_probe_appends_an_auditable_gate_record(store_factory) -> None:
    now = {"t": _T0}
    store = store_factory(None, _clock(now))
    store.probe()

    gate = store.query(kind="evidence_gate")
    assert len(gate) == 1
    assert gate[0].outcome == "ok"


def test_retention_expires_old_records_and_leaves_a_trace(store_factory) -> None:
    now = {"t": _T0}
    store = store_factory(EvidencePolicy(retention_days=7), _clock(now))
    store.append("execution", "app-reader", "run-old", "ok", {})
    now["t"] += 8 * _DAY
    store.append("execution", "app-reader", "run-new", "ok", {})

    assert [record.ref for record in store.query(kind="execution")] == ["run-new"]
    trace = store.query(kind="evidence_retention")
    assert len(trace) == 1
    assert trace[0].detail.get("deleted_files", trace[0].detail.get("deleted_records", 0)) >= 1


def test_capacity_rejects_when_cleanup_cannot_help(store_factory) -> None:
    now = {"t": _T0}
    store = store_factory(None, _clock(now))
    store.append("execution", "app-reader", "run-1", "ok", {})
    store.append("execution", "app-reader", "run-2", "ok", {})
    limit = store.usage() - 1

    store = store_factory(EvidencePolicy(capacity_limit=limit), _clock(now))
    with pytest.raises(EvidenceCapacityError) as excinfo:
        store.probe()
    assert excinfo.value.category == CATEGORY_CAPACITY
    assert excinfo.value.limit == limit


def test_capacity_runs_retention_cleanup_before_refusing_then_recovers(store_factory) -> None:
    now = {"t": _T0}
    store = store_factory(None, _clock(now))
    store.append("execution", "app-reader", "run-old", "ok", {})
    limit = store.usage() - 1

    store = store_factory(EvidencePolicy(retention_days=7, capacity_limit=limit), _clock(now))
    with pytest.raises(EvidenceCapacityError):
        store.probe()  # over the limit, but nothing is expired yet

    now["t"] += 8 * _DAY
    store.probe()  # cleanup frees the expired record; the gate reopens
    assert store.query(kind="execution") == []
    assert store.query(kind="evidence_gate")[0].outcome == "ok"


def test_gate_failure_category_is_recorded_and_reported(store_factory) -> None:
    now = {"t": _T0}
    store = store_factory(None, _clock(now))
    store.record_gate_failure(CATEGORY_UNWRITABLE, "evidence device full")

    summary = store.policy_summary()
    assert summary["evidence_last_gate_failure"]["category"] == CATEGORY_UNWRITABLE
    denial = store.query(kind="evidence_gate", outcome="denied")
    assert len(denial) == 1
    assert denial[0].detail["category"] == CATEGORY_UNWRITABLE


def test_policy_summary_reports_backend_units_and_explicit_defaults(store_factory) -> None:
    now = {"t": _T0}
    store = store_factory(None, _clock(now))
    summary = store.policy_summary()

    assert summary["evidence_backend"] in ("jsonl", "durable")
    assert summary["evidence_unit"] in ("bytes", "records")
    assert summary["evidence_retention_days"] is None
    assert summary["evidence_capacity_limit"] is None
    assert summary["evidence_usage"] >= 0
    assert "evidence_last_gate_failure" not in summary


# --- backend resolution and SQL assembly shape ---------------------------------------


def test_backend_resolution() -> None:
    assert evidence_backend("jsonl") is EvidenceStore
    assert evidence_backend("durable").backend == "durable"
    with pytest.raises(ValueError, match="unknown evidence backend"):
        evidence_backend("s3")


def test_sql_evidence_shares_the_durable_engine(tmp_path: Path) -> None:
    from hecate_durable.storage import SqlDurableStore
    from hecate_runner.evidence_sql import SqlEvidenceStore

    url = f"sqlite:///{tmp_path / 'runner-state.db'}"
    store = SqlDurableStore(url)
    store.create_schema()
    evidence = SqlEvidenceStore(store.engine, policy=EvidencePolicy(retention_days=7, capacity_limit=100))
    evidence.create_schema()

    evidence.append("execution", "app-reader", "run-1", "ok", {"tool": "query_inventory"})
    assert evidence.usage() == 1
    assert evidence.query()[0].detail == {"tool": "query_inventory"}
    assert evidence.policy_summary()["evidence_backend"] == "durable"
    evidence.dispose()  # shared engine: dispose must be a no-op on ownership
    store.dispose()


# --- profile wiring -------------------------------------------------------------------


def test_evidence_block_parses_policy_and_dir_override(tmp_path: Path, shutdown_token: None) -> None:
    profile = load_profile(
        write_profile(tmp_path, evidence={"dir": "audit", "retention_days": 30, "capacity_limit": 5000})
    )
    evidence = profile.config.evidence
    assert evidence is not None
    assert evidence.backend == "jsonl"
    assert evidence.dir_override == tmp_path / "profile" / "audit"
    assert evidence.retention_days == 30
    assert evidence.capacity_limit == 5000
    summary = profile.capabilities_summary()
    assert summary["evidence_policy_configured"] is True
    assert summary["evidence_retention_days"] == 30


def test_evidence_block_absent_reports_explicit_default(tmp_path: Path, shutdown_token: None) -> None:
    profile = load_profile(write_profile(tmp_path))
    assert profile.config.evidence is None
    summary = profile.capabilities_summary()
    assert summary["evidence_backend"] == "jsonl"
    assert summary["evidence_retention_days"] is None
    assert summary["evidence_capacity_limit"] is None
    assert summary["evidence_policy_configured"] is False


@pytest.mark.parametrize(
    ("block", "message"),
    [
        ({"backend": "s3"}, "evidence.backend must be"),
        ({"retention_days": 0}, "evidence.retention_days must be a positive integer"),
        ({"capacity_limit": True}, "evidence.capacity_limit must be a positive integer"),
        ({"dir": ""}, "evidence.dir must be a non-empty string"),
        ("nope", "evidence must be an object"),
    ],
)
def test_evidence_invalid_blocks_fail_startup(tmp_path: Path, block: dict | str, message: str) -> None:
    with pytest.raises(ProfileError, match=message):
        load_profile(write_profile(tmp_path, evidence=block))


def test_evidence_durable_backend_requires_durable_profile(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="requires the durable profile"):
        load_profile(write_profile(tmp_path, evidence={"backend": "durable"}))


def test_evidence_durable_backend_with_durable_profile(tmp_path: Path, shutdown_token: None) -> None:
    profile = load_profile(
        write_profile(
            tmp_path,
            durable={"database_url": f"sqlite:///{tmp_path / 'state.db'}", "workspace": "standalone"},
            evidence={"backend": "durable"},
        )
    )
    assert profile.config.evidence is not None
    assert profile.config.evidence.backend == "durable"
