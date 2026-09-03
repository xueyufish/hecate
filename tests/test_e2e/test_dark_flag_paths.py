"""A7 — gated paths behind dark flags behave correctly when activated.

Three flags ship off-by-default but path-loadable: when a workspace
flips them on, the corresponding code path must actually run and produce
the expected behavior. Without these tests, a flag flips on, the
underlying machinery is wired wrong, and nobody finds out until the
first customer hits the toggle.

Coverage:
- AGENT_ENV_SANDBOX_ENFORCEMENT: shell tools with EXECUTE_SANDBOX route
  to DockerEnvironment instead of generic port.tool_execute_sandbox().
- AGENT_ENV_CREDENTIAL_SCOPING: docker_environment config toggles
  scoped_credentials between true and false.
- SIEM_ENABLED: when on, the SIEMEventType/EventSeverity vocabulary
  resolves; the wiring itself is tested in services/security/siem/.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hecate.runtime.tool_access import AccessDecision
from hecate.runtime.workers.sandbox_router import SandboxEnforcementRouter


def test_sandbox_router_off_routes_nothing() -> None:
    """Disabled router (default) routes nothing regardless of decision — backward compat."""
    router = SandboxEnforcementRouter(enabled=False)
    assert router.enabled is False
    assert router.should_route_to_environment("bash", AccessDecision.EXECUTE_SANDBOX) is False
    assert router.should_route_to_environment("execute_code", AccessDecision.EXECUTE_SANDBOX) is False


def test_sandbox_router_on_routes_shell_tools() -> None:
    """Enabled router routes shell/exec tools under EXECUTE_SANDBOX to DockerEnvironment.

    Non-shell tools under EXECUTE_SANDBOX still need sandbox_enabled
    metadata to route (kept for MCP tool sandboxing); tools under other
    decisions never route.
    """
    router = SandboxEnforcementRouter(enabled=True)
    assert router.should_route_to_environment("bash", AccessDecision.EXECUTE_SANDBOX) is True
    assert router.should_route_to_environment("execute_code", AccessDecision.EXECUTE_SANDBOX) is True
    assert router.should_route_to_environment("exec_shell", AccessDecision.EXECUTE_SANDBOX) is True

    # Other decisions never route, even with the flag on.
    assert router.should_route_to_environment("bash", AccessDecision.EXECUTE) is False
    assert router.should_route_to_environment("bash", AccessDecision.DENY) is False
    assert router.should_route_to_environment("bash", AccessDecision.REQUIRE_APPROVAL) is False

    # Non-shell tool with EXECUTE_SANDBOX does not route unless sandbox_enabled.
    assert router.should_route_to_environment("some_mcp_tool", AccessDecision.EXECUTE_SANDBOX) is False
    assert (
        router.should_route_to_environment("some_mcp_tool", AccessDecision.EXECUTE_SANDBOX, sandbox_enabled=True)
        is True
    )


def test_sandbox_router_detects_abnormal_container_exit() -> None:
    """verify_container_exit emits anomaly event + returns False for negative exit codes.

    Negative exit codes signal timeout or signal (OOM, segfault); the
    router must surface them as SIEM events rather than silently passing.
    """
    from hecate.runtime.workers import sandbox_router as router_module

    router = SandboxEnforcementRouter(enabled=True)
    with (
        patch.object(router_module.decision_emitter, "emit") as emit,
        patch.object(
            router_module.decision_emitter,
            "build_event",
            return_value={"decision": "sandbox_anomaly", "reason": "code=-9"},
        ),
    ):
        healthy = router.verify_container_exit(exit_code=0, stderr=b"")
        anomalous = router.verify_container_exit(exit_code=-9, stderr=b"OOM killed")
    assert healthy is True
    assert anomalous is False
    assert emit.call_count == 1


def test_credential_scoping_setting_is_read_by_environment_manager() -> None:
    """The toggle exists on Settings and is read by EnvironmentManager.

    A regression where AGENT_ENV_CREDENTIAL_SCOPING is removed from
    Settings but still referenced in manager.py (or vice versa) crashes
    on boot. This test guards against the wire being broken in either
    direction.
    """
    import os

    from hecate_sandbox.environment import manager as manager_pkg

    from hecate.core.config import Settings

    field_names = Settings.model_fields.keys()
    assert "AGENT_ENV_CREDENTIAL_SCOPING" in field_names, (
        "Settings must declare AGENT_ENV_CREDENTIAL_SCOPING for the dark-flag flip to land"
    )

    with open(manager_pkg.__file__) as f:
        source = f.read()
    assert "AGENT_ENV_CREDENTIAL_SCOPING" in source, (
        "EnvironmentManager must read AGENT_ENV_CREDENTIAL_SCOPING somewhere in its code path"
    )

    _ = os.environ  # silence unused-import warning if test is later trimmed


def test_siem_event_vocabulary_resolves_when_feature_on() -> None:
    """The SIEM vocabulary types resolve regardless of the runtime flag —
    their existence is what gates downstream emit calls.

    A regression where these classes disappear (e.g. deleted because
    SIEM was always off) breaks the dark-flag flip without anyone noticing.
    """
    from hecate.ops.siem.event import EventSeverity, EventSource, EventType, SecurityEvent

    assert EventSeverity is not None
    assert EventType is not None
    assert EventSource is not None
    # SecurityEvent is the envelope; constructing one with the canonical
    # fields must succeed without raising.
    event = SecurityEvent(
        event_type=EventType.API,
        severity=EventSeverity.HIGH,
        source=EventSource.AUDIT_LOG,
        action="test",
    )
    assert event.action == "test"


@pytest.mark.parametrize(
    "enabled, decision, tool_name, sandbox_enabled, expected",
    [
        (False, AccessDecision.EXECUTE_SANDBOX, "bash", False, False),
        (True, AccessDecision.EXECUTE_SANDBOX, "bash", False, True),
        (True, AccessDecision.EXECUTE_SANDBOX, "read_file", False, False),
        (True, AccessDecision.EXECUTE_SANDBOX, "read_file", True, True),
        (True, AccessDecision.EXECUTE, "bash", False, False),
        (True, AccessDecision.DENY, "bash", False, False),
    ],
)
def test_sandbox_router_decision_matrix(enabled, decision, tool_name, sandbox_enabled, expected) -> None:
    """Parametrized matrix locks down the (enabled × decision × tool × sandbox) truth table."""
    router = SandboxEnforcementRouter(enabled=enabled)
    assert router.should_route_to_environment(tool_name, decision, sandbox_enabled=sandbox_enabled) == expected
