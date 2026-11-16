"""Offline regression for Docker environment reuse and policy enforcement."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiodocker.exceptions import DockerError
from hecate_sandbox.environment.docker_environment import DockerEnvironment
from hecate_sandbox.environment.environment import ExecResult
from hecate_sandbox.environment.manager import EnvironmentManager
from hecate_sandbox.environment.network_policy import (
    NetworkEgressPolicy,
    NetworkPolicyMode,
)

from hecate.core.config import settings

AGENT = "policy-agent-1"
LEGACY_NETWORK = f"hecate-internal-{AGENT[:12]}"


# ---------------------------------------------------------------------------
# Fake aiodocker client: enough surface for the environment lifecycle
# ---------------------------------------------------------------------------


class FakeContainer:
    def __init__(self, store: FakeStore, name: str, info: dict[str, Any]) -> None:
        self._store = store
        self._name = name
        self._info = info

    async def show(self) -> dict[str, Any]:
        return self._info

    async def start(self) -> None:
        self._info["State"]["Status"] = "running"

    async def stop(self) -> None:
        self._info["State"]["Status"] = "exited"

    async def delete(self, force: bool = False) -> None:
        self._store.containers.pop(self._name, None)


class FakeStore:
    """In-memory Docker state shared by one test's client objects."""

    def __init__(self) -> None:
        self.containers: dict[str, FakeContainer] = {}
        self.volumes: set[str] = set()
        self.created_containers: list[dict[str, Any]] = []

    def seed_container(
        self, name: str, *, image: str, network: str, volume: str, running: bool = True
    ) -> FakeContainer:
        info = _inspect_record(image=image, network=network, volume=volume, running=running)
        container = FakeContainer(self, name, info)
        self.containers[name] = container
        return container


class FakeContainers:
    def __init__(self, store: FakeStore) -> None:
        self._store = store

    async def get(self, name: str) -> FakeContainer:
        if name not in self._store.containers:
            raise DockerError(404, f"container {name} not found")
        return self._store.containers[name]

    async def create(self, config: dict[str, Any], *, name: str) -> FakeContainer:
        if name in self._store.containers:
            raise DockerError(409, f"container {name} already exists")
        host = config["HostConfig"]
        (mount,) = host["Mounts"]
        info = _inspect_record(
            image=config["Image"], network=host["NetworkMode"], volume=mount["Source"], running=False
        )
        self._store.containers[name] = FakeContainer(self._store, name, info)
        self._store.created_containers.append({"name": name, "config": config})
        return self._store.containers[name]


class FakeVolumes:
    def __init__(self, store: FakeStore) -> None:
        self._store = store

    async def create(self, config: dict[str, Any]) -> None:
        self._store.volumes.add(config["Name"])


class FakeDocker:
    def __init__(self, store: FakeStore, url: str | None = None) -> None:
        self.store = store
        self.containers = FakeContainers(store)
        self.volumes = FakeVolumes(store)

    async def close(self) -> None:
        return None


def _inspect_record(*, image: str, network: str, volume: str, running: bool = True) -> dict[str, Any]:
    return {
        "Name": "/name",
        "Config": {"Image": image},
        "HostConfig": {"NetworkMode": network, "Runtime": "runc"},
        "State": {"Status": "running" if running else "exited"},
        "Mounts": [{"Type": "volume", "Name": volume, "Destination": "/env", "RW": True}],
        "NetworkSettings": {"Networks": {network: {}}},
    }


@pytest.fixture
def fake_docker(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    """Route every aiodocker.Docker() construction to one shared FakeStore."""
    store = FakeStore()

    class _BoundFakeDocker(FakeDocker):
        def __init__(self, url: str | None = None) -> None:  # noqa: ARG001
            super().__init__(store)

    import aiodocker

    monkeypatch.setattr(aiodocker, "Docker", _BoundFakeDocker)
    return store


@pytest.fixture
def exec_free(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace container exec with a no-op so ensure_dirs runs the real lifecycle."""

    async def _noop_exec(self: DockerEnvironment, command: list[str], **_kwargs: Any) -> ExecResult:
        return ExecResult(exit_code=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(DockerEnvironment, "exec_shell", _noop_exec)


@pytest.fixture
def docker_settings(monkeypatch: pytest.MonkeyPatch):
    """The docker backend with the loose policy; returns a policy tightener."""

    def _set(**kwargs: Any) -> None:
        for key, value in kwargs.items():
            monkeypatch.setattr(settings, key, value)

    _set(
        AGENT_ENV_BACKEND="docker",
        AGENT_ENV_NETWORK_POLICY="allow_all",
        AGENT_ENV_CREDENTIAL_SCOPING=False,
        AGENT_ENV_SANDBOX_ENFORCEMENT=False,
        DOCKER_NETWORK_MODE="bridge",
        DOCKER_RUNTIME="runc",
    )

    def tighten() -> None:
        _set(AGENT_ENV_NETWORK_POLICY="deny_all")

    return tighten


# ---------------------------------------------------------------------------
# Hot cache and warm pool honor policy changes
# ---------------------------------------------------------------------------


async def test_policy_tightening_replaces_the_cached_container(
    fake_docker: FakeStore, docker_settings, exec_free
) -> None:
    manager = EnvironmentManager()
    env_before = await manager.get_or_create(AGENT)
    assert isinstance(env_before, DockerEnvironment)
    old_name = env_before._container_name
    assert old_name in fake_docker.containers

    docker_settings()
    env_after = await manager.get_or_create(AGENT)

    assert env_after is not env_before
    # The old container was replaced under the same name: the store now
    # holds a different container, created with the verified none
    # network.
    assert fake_docker.containers[old_name] is not env_before._container
    assert env_after._container is not env_before._container
    first, second = fake_docker.created_containers
    assert first["config"]["HostConfig"]["NetworkMode"] == "bridge"
    assert second["config"]["HostConfig"]["NetworkMode"] == "none"
    with pytest.raises(RuntimeError, match="retired"):
        await env_before._ensure_started()
    await manager.close_all()


async def test_warm_pool_policy_tightening_destroys_the_stale_container(
    fake_docker: FakeStore, docker_settings, exec_free, monkeypatch
) -> None:
    manager = EnvironmentManager()
    env_before = await manager.get_or_create(AGENT)
    old_name = env_before._container_name
    await manager.close(AGENT)  # into the warm pool, container kept
    assert old_name in fake_docker.containers

    monkeypatch.setattr(settings, "AGENT_ENV_NETWORK_POLICY", "deny_all")
    env_after = await manager.get_or_create(AGENT)

    assert env_after is not env_before
    assert fake_docker.containers[old_name] is not env_before._container
    assert fake_docker.created_containers[-1]["config"]["HostConfig"]["NetworkMode"] == "none"
    await manager.close_all()


# ---------------------------------------------------------------------------
# Process restart: stale containers discovered by name
# ---------------------------------------------------------------------------


async def test_restart_replaces_a_stale_loose_policy_container(
    fake_docker: FakeStore, docker_settings, exec_free, monkeypatch
) -> None:
    """A container left by a previous process under allow_all is replaced."""
    monkeypatch.setattr(settings, "AGENT_ENV_NETWORK_POLICY", "deny_all")
    stale = fake_docker.seed_container(
        f"hecate-agent-{AGENT}", image=settings.DOCKER_AGENT_IMAGE, network="bridge", volume=f"agent-{AGENT}"
    )

    manager = EnvironmentManager()
    env = await manager.get_or_create(AGENT)
    assert isinstance(env, DockerEnvironment)
    assert env._container is not stale
    assert fake_docker.containers[stale._name] is not stale
    (created,) = fake_docker.created_containers
    assert created["config"]["HostConfig"]["NetworkMode"] == "none"
    await manager.close_all()


async def test_restart_reuses_a_compliant_container(
    fake_docker: FakeStore, docker_settings, exec_free, monkeypatch
) -> None:
    """A container already on the none network is reused."""
    monkeypatch.setattr(settings, "AGENT_ENV_NETWORK_POLICY", "deny_all")
    compliant = fake_docker.seed_container(
        f"hecate-agent-{AGENT}",
        image=settings.DOCKER_AGENT_IMAGE,
        network="none",
        volume=f"agent-{AGENT}",
    )

    manager = EnvironmentManager()
    env = await manager.get_or_create(AGENT)
    assert isinstance(env, DockerEnvironment)
    assert env._container is compliant
    assert fake_docker.created_containers == []
    await manager.close_all()


async def test_restart_replaces_deny_all_container_after_a_rollback(
    fake_docker: FakeStore, docker_settings, exec_free
) -> None:
    stale = fake_docker.seed_container(
        f"hecate-agent-{AGENT}", image=settings.DOCKER_AGENT_IMAGE, network="none", volume=f"agent-{AGENT}"
    )
    manager = EnvironmentManager()
    env = await manager.get_or_create(AGENT)
    assert env._container is not stale
    assert fake_docker.created_containers[-1]["config"]["HostConfig"]["NetworkMode"] == "bridge"
    await manager.close_all()


async def test_restart_refuses_an_unverifiable_container_record(
    fake_docker: FakeStore, docker_settings, exec_free, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "AGENT_ENV_NETWORK_POLICY", "deny_all")
    broken = fake_docker.seed_container(
        f"hecate-agent-{AGENT}", image=settings.DOCKER_AGENT_IMAGE, network="none", volume=f"agent-{AGENT}"
    )
    broken._info["NetworkSettings"] = None  # the record cannot be verified

    manager = EnvironmentManager()
    with pytest.raises(RuntimeError, match="cannot verify container"):
        await manager.get_or_create(AGENT)
    await manager.close_all()


# ---------------------------------------------------------------------------
# Network attribute verification
# ---------------------------------------------------------------------------


async def test_legacy_dual_homed_container_is_replaced(fake_docker: FakeStore, docker_settings, exec_free) -> None:
    docker_settings()
    stale = fake_docker.seed_container(
        f"hecate-agent-{AGENT}", image=settings.DOCKER_AGENT_IMAGE, network=LEGACY_NETWORK, volume=f"agent-{AGENT}"
    )
    stale._info["NetworkSettings"]["Networks"]["bridge"] = {}
    manager = EnvironmentManager()
    env = await manager.get_or_create(AGENT)
    assert env._container is not stale
    (created,) = fake_docker.created_containers
    assert created["config"]["HostConfig"]["NetworkMode"] == "none"
    await manager.close_all()


async def test_inspect_failure_does_not_create_or_replace(
    fake_docker: FakeStore, docker_settings, exec_free, monkeypatch
) -> None:
    docker_settings()
    existing = fake_docker.seed_container(
        f"hecate-agent-{AGENT}", image=settings.DOCKER_AGENT_IMAGE, network="bridge", volume=f"agent-{AGENT}"
    )

    async def fail_inspect() -> dict[str, Any]:
        raise DockerError(500, "inspection failed")

    monkeypatch.setattr(existing, "show", fail_inspect)
    manager = EnvironmentManager()
    with pytest.raises(DockerError, match="inspection failed"):
        await manager.get_or_create(AGENT)
    assert fake_docker.containers[existing._name] is existing
    assert fake_docker.created_containers == []
    await manager.close_all()


async def test_same_name_container_with_foreign_volume_is_left_alone(
    fake_docker: FakeStore, docker_settings, exec_free
) -> None:
    docker_settings()
    existing = fake_docker.seed_container(
        f"hecate-agent-{AGENT}", image=settings.DOCKER_AGENT_IMAGE, network="none", volume="other-volume"
    )
    manager = EnvironmentManager()
    with pytest.raises(RuntimeError, match="expected writable agent volume"):
        await manager.get_or_create(AGENT)
    assert fake_docker.containers[existing._name] is existing
    assert fake_docker.created_containers == []
    await manager.close_all()


# ---------------------------------------------------------------------------
# Default-deny iptables rule
# ---------------------------------------------------------------------------


async def test_allow_list_rules_carry_the_default_deny(monkeypatch) -> None:
    policy = NetworkEgressPolicy(
        mode=NetworkPolicyMode.DENY_ALL,
        allowed_domains=["example.com"],
    )
    captured: dict[str, list[str]] = {}

    class _Proc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"", b"")

    async def _fake_exec(*cmd: Any, **_kwargs: Any) -> _Proc:
        captured["cmd"] = [str(part) for part in cmd]
        return _Proc()

    async def _fake_getaddrinfo(_host: str, _port: Any, *args: Any, **kwargs: Any) -> list[Any]:
        # (family, type, proto, canonname, sockaddr) with AF_INET=2, SOCK_STREAM=1.
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo)

    assert await policy.apply_to_container("cid-abc") is True
    cmd = " ".join(captured["cmd"])
    assert "iptables -A OUTPUT -d 93.184.216.34 -j ACCEPT" in cmd
    assert "iptables -A OUTPUT -j REJECT --reject-with icmp-net-unreachable" in cmd
    assert "ip6tables -A OUTPUT -j REJECT" in cmd


# ---------------------------------------------------------------------------
# Manager hash plumbing
# ---------------------------------------------------------------------------


async def test_local_backend_survives_a_policy_change(tmp_path, monkeypatch) -> None:
    """The Docker-only setting does not discard a cached local environment."""
    manager = EnvironmentManager(root=str(tmp_path), backend="local")
    env_before = await manager.get_or_create(AGENT)
    monkeypatch.setattr(settings, "AGENT_ENV_NETWORK_POLICY", "deny_all")
    env_after = await manager.get_or_create(AGENT)
    assert env_after is env_before
    await manager.close_all()
