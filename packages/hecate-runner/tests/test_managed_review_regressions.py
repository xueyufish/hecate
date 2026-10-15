"""Exercise the managed CLI's assembled authorization chain."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest_runner import write_profile
from hecate_durable.contracts.credentials import lease_claims
from hecate_governance.action import target_resources_for, tool_arguments_digest
from hecate_governance.approval import ApprovalBinding
from hecate_governance.clock import TrustedClock
from hecate_governance.principal import ActionPrincipal, PrincipalSource
from hecate_runner import __main__ as cli
from hecate_runner.engine import RunState
from hecate_runner.local_approvals import DurableLocalApprovalLedger
from hecate_runner.managed import ManagedChannel


@pytest.mark.parametrize(
    "case",
    [
        "approved",
        "missing",
        "revoked",
        "trust_revoked",
        "hard_deny",
        "scope",
        "standalone",
        "expired_approval",
        "wrong_task",
        "unregistered_signer",
    ],
)
async def test_cli_authorization_chain(case, tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown")
    monkeypatch.setenv("RUNNER_LEASE_SECRET", "lease-secret")
    directory = write_profile(
        tmp_path,
        allowlist=["submit_inventory_update"],
        durable={"database_url": f"sqlite:///{tmp_path / 'host.db'}", "workspace": "ws"},
        with_write_tool=True,
    )
    config_path = directory / "runner.json"
    config = json.loads(config_path.read_text())
    config["governance"] = {
        "write_enablement": "disabled" if case == "hard_deny" else "experimental",
        "write_attestation": "business-api-enforces-final-authorization",
        "approvers": ["approver"],
        "revoked_principals": ["reader"] if case == "revoked" else [],
        "rules": [
            {
                "tool": "submit_inventory_update",
                "domains": ["domain_a", "domain_b"],
                "approval_required": case != "standalone",
            }
        ],
    }
    config["control_plane"] = {
        "base_url": "http://platform.test",
        "workspace_id": "00000000-0000-0000-0000-000000000001",
        "trust_root": "root",
        "host_id": "host",
        "issuer_domain": "issuer",
        "secret_ref": "env:RUNNER_LEASE_SECRET",
        "data_domains": ["domain_a", "domain_b"],
        "lease_refresh_wait_seconds": 0.01,
        "lease_policy": {"policy_version": "v1", "max_staleness_seconds": 10},
    }
    config_path.write_text(json.dumps(config))
    captured = {}
    original_channel = cli.ManagedChannel

    def capture_channel(**kwargs):
        channel = original_channel(**kwargs)
        channel.authorization_revoked = case == "trust_revoked"
        captured["channel"] = channel
        return channel

    monkeypatch.setattr(cli, "ManagedChannel", capture_channel)

    class AssembledError(Exception):
        pass

    def capture(profile, engine, evidence, **kwargs):
        captured.update(engine=engine, durable=kwargs["durable"])
        raise AssembledError

    monkeypatch.setattr(cli, "RunnerServer", capture)
    with pytest.raises(AssembledError):
        cli.main(["--profile", str(directory), "--business-api", "http://business.test"])
    engine = captured["engine"]
    durable = captured["durable"]
    calls = []

    async def dispatch(*args):
        calls.append(args)
        return {"status": "ok", "result": {}}

    engine._tool_dispatch = dispatch
    arguments = {"domain": "domain_b" if case == "scope" else "domain_a", "sku": "SKU", "quantity": 1}
    principal = ActionPrincipal.from_verified_claims(
        {"sub": "reader"}, source=PrincipalSource.RUNNER_LOCAL, domains=("domain_a", "domain_b")
    )
    authority = DurableLocalApprovalLedger(durable.store.session_factory, approvers=("approver",))
    if case != "missing":
        await authority.save(
            ApprovalBinding(
                approval_ref="approved-action",
                principal_fingerprint=principal.fingerprint(),
                tool_name="submit_inventory_update",
                arguments_digest=tool_arguments_digest(arguments),
                target_resources=target_resources_for(arguments),
                domains=principal.domains,
                issued_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=-1 if case == "expired_approval" else 10),
                judgment="approved",
                decided_by="other" if case == "unregistered_signer" else "approver",
                task_scope="another-task" if case == "wrong_task" else None,
            )
        )
        if case == "approved":
            # A newer binding for another task must not shadow the matching one.
            matching = await authority.get("approved-action")
            await authority.save(
                matching.model_copy(
                    update={"approval_ref": "another-task", "task_scope": "other", "issued_at": datetime.now(UTC)}
                )
            )
    gate = engine._lease_gate
    moment = datetime.now(UTC) - timedelta(seconds=30)
    wall = [moment]
    mono = [100.0]
    gate._clock = lambda: wall[0]
    gate._trusted_clock = TrustedClock(now_fn=lambda: wall[0], monotonic_fn=lambda: mono[0])
    lease, nonce = lease_claims(
        b"lease-secret",
        iss="issuer",
        deployment_domain="root",
        sub="host",
        tenant=config["control_plane"]["workspace_id"],
        scope=["domain_a"],
        policy_version="v1",
        ttl_seconds=10,
        now=moment,
    )
    gate.update(lease)
    wall[0] += timedelta(seconds=30)
    mono[0] += 30
    try:
        state = RunState("r1", "running", "reader", principal.domains, managed=case != "standalone")
        result = await engine._dispatch_tool(state, "submit_inventory_update", arguments)
        assert len(calls) == (1 if case in ("approved", "standalone") else 0)
        assert result["status"] == ("ok" if calls else "authorization")
        assert nonce not in gate._consumed
        if case == "approved":
            binding = await authority.get("approved-action")
            assert binding.consumed_by_invocation_id is not None
            # Another logical action cannot reuse the approval.
            second = RunState("r2", "running", "reader", principal.domains, managed=True)
            assert (await engine._dispatch_tool(second, "submit_inventory_update", arguments))[
                "status"
            ] == "authorization"
            assert len(calls) == 1
    finally:
        durable.store.dispose()


async def test_upload_success_does_not_clear_drift_or_known_revocation(tmp_path):
    from hecate_durable.storage import SqlDurableStore

    store = SqlDurableStore(f"sqlite:///{tmp_path / 'channel.db'}")
    store.create_schema()
    status = [409]

    async def respond(request):
        if status[0] == 409:
            return httpx.Response(409, json={"detail": {"error": {"code": "config-drift", "message": "changed"}}})
        if status[0] == 403:
            return httpx.Response(403, json={"detail": {"error": {"code": "enrollment-inactive"}}})
        return httpx.Response(status[0], json={})

    channel = ManagedChannel(
        base_url="http://platform.test",
        workspace_id="ws",
        trust_root="root",
        host_id="host",
        secret=b"secret",
        issuer_domain="issuer",
        store=store,
        transport=httpx.MockTransport(respond),
    )
    channel._credential = {"test": "verified-by-transport"}
    try:
        await channel._request("POST", "/managed/host/accept", tolerate=(409,))
        assert channel.last_config_drift == "changed"
        assert channel.stats.config_drift_rejections == 1
        status[0] = 403
        await channel._request("GET", "/managed/host/pull")
        assert channel.authorization_revoked
        status[0] = 200
        await channel._request("POST", "/managed/host/events")
        assert channel.last_config_drift == "changed"
        assert channel.authorization_revoked
        await channel._request("GET", "/managed/host/pull?config_fingerprint=correct")
        assert channel.last_config_drift is None
        assert not channel.authorization_revoked
        status[0] = 401
        await channel._request("GET", "/managed/host/pull")
        assert channel._credential is None
    finally:
        store.dispose()
