"""Fixture-level verification for the standalone-consumption inventory stub.

These tests verify the stub's own business rules (domain isolation, read-only
enforcement, one-shot parameter-bound approvals, queryable denials) so SC
scenarios can rely on them. They are NOT standalone-runner tests: no Hecate
host, wheel, or execution backend is involved, and no independent-host
capability is claimed. The function prefix ``test_sc_fixture_`` deliberately
matches neither the S-scenario binding (``test_s<nn>_``) nor the future
SC-scenario binding (``test_sc<nn>_``) enforced by test_manifest_consistency.py.
"""

from __future__ import annotations

from pathlib import Path

from tests.scenarios.tools.inventory_api import (
    InventoryOutcome,
    StubInventoryApi,
)

SCENARIO_DIR = Path(__file__).resolve().parent


def _make_service() -> StubInventoryApi:
    return StubInventoryApi()


def test_sc_fixture_cross_domain_read_is_denied() -> None:
    service = _make_service()
    reader_a = service.make_read_only_identity("reader_a", "domain_a")

    record = service.read("domain_b", "SKU-B1", reader_a)

    assert record is None
    denied = service.calls_by_outcome(InventoryOutcome.DENIED_CROSS_DOMAIN)
    assert len(denied) == 1
    assert denied[0].domain == "domain_b"
    assert denied[0].identity == "reader_a"


def test_sc_fixture_domains_are_mutually_invisible() -> None:
    service = _make_service()
    reader_a = service.make_read_only_identity("reader_a", "domain_a")

    # Within-domain reads succeed; the other domain's SKUs are unreachable.
    assert service.read("domain_a", "SKU-A1", reader_a) == {"name": "示例商品A1", "quantity": 100}
    # A forged SKU from the other domain yields nothing, never its content.
    assert service.read("domain_a", "SKU-B1", reader_a) is None
    assert service.read("domain_b", "SKU-A1", reader_a) is None


def test_sc_fixture_read_only_identity_cannot_write() -> None:
    service = _make_service()
    reader_a = service.make_read_only_identity("reader_a", "domain_a")
    service.approve_write("domain_a", "SKU-A1", 5)

    outcome = service.write("domain_a", "SKU-A1", 5, reader_a)

    assert outcome is InventoryOutcome.DENIED_READ_ONLY
    assert service.quantity("domain_a", "SKU-A1") == 100


def test_sc_fixture_unapproved_write_changes_nothing() -> None:
    service = _make_service()
    writer = service.make_writer_identity("writer", "domain_a")

    outcome = service.write("domain_a", "SKU-A1", 5, writer)

    assert outcome is InventoryOutcome.DENIED_UNAPPROVED
    assert service.quantity("domain_a", "SKU-A1") == 100
    assert len(service.calls_by_outcome(InventoryOutcome.DENIED_UNAPPROVED)) == 1


def test_sc_fixture_approved_write_executes_exactly_once() -> None:
    service = _make_service()
    writer = service.make_writer_identity("writer", "domain_a")

    service.approve_write("domain_a", "SKU-A1", 5)
    outcome = service.write("domain_a", "SKU-A1", 5, writer)

    assert outcome is InventoryOutcome.OK
    assert service.quantity("domain_a", "SKU-A1") == 5
    ok_writes = [call for call in service.calls() if call.operation == "write" and call.outcome is InventoryOutcome.OK]
    assert len(ok_writes) == 1


def test_sc_fixture_repeated_write_after_approval_does_not_execute_again() -> None:
    service = _make_service()
    writer = service.make_writer_identity("writer", "domain_a")

    service.approve_write("domain_a", "SKU-A1", 5)
    assert service.write("domain_a", "SKU-A1", 5, writer) is InventoryOutcome.OK
    # The one-shot approval was consumed: the replay is unapproved and changes
    # nothing.
    assert service.write("domain_a", "SKU-A1", 5, writer) is InventoryOutcome.DENIED_UNAPPROVED
    assert service.quantity("domain_a", "SKU-A1") == 5


def test_sc_fixture_write_to_foreign_domain_is_denied_even_for_writer() -> None:
    service = _make_service()
    writer = service.make_writer_identity("writer", "domain_a")
    service.approve_write("domain_b", "SKU-B1", 1)

    outcome = service.write("domain_b", "SKU-B1", 1, writer)

    assert outcome is InventoryOutcome.DENIED_CROSS_DOMAIN
    assert service.quantity("domain_b", "SKU-B1") == 7


def test_sc_fixture_denials_are_queryable_evidence() -> None:
    service = _make_service()
    reader_a = service.make_read_only_identity("reader_a", "domain_a")
    writer_a = service.make_writer_identity("writer_a", "domain_a")

    service.read("domain_b", "SKU-B1", reader_a)
    service.write("domain_a", "SKU-A1", 5, reader_a)
    service.write("domain_a", "SKU-A1", 5, writer_a)

    outcomes = [call.outcome for call in service.calls()]
    assert InventoryOutcome.DENIED_CROSS_DOMAIN in outcomes
    assert InventoryOutcome.DENIED_READ_ONLY in outcomes
    assert InventoryOutcome.DENIED_UNAPPROVED in outcomes
    for call in service.calls():
        assert call.operation in {"read", "write"}
        assert call.identity


def test_sc_fixture_stays_self_contained() -> None:
    """The stub imports no Hecate platform code, RAG, or Memory packages."""
    source = (SCENARIO_DIR / "tools" / "inventory_api.py").read_text(encoding="utf-8")
    import_lines = [line.strip() for line in source.splitlines() if line.strip().startswith(("import ", "from "))]
    assert import_lines, "inventory stub must have import lines to inspect"
    for line in import_lines:
        for forbidden in ("hecate", "sqlalchemy", "fastapi", "pydantic", "yaml"):
            assert forbidden not in line, f"inventory stub must not import {forbidden}: {line}"
