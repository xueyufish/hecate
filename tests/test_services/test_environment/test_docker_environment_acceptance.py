"""Real Linux Docker acceptance for network and credential isolation."""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

import pytest
from hecate_sandbox.environment.credential_scope import CredentialScope
from hecate_sandbox.environment.docker_environment import DockerEnvironment
from hecate_sandbox.environment.manager import EnvironmentManager
from hecate_sandbox.environment.network_policy import NetworkEgressPolicy, NetworkPolicyMode

RECEIVER_PORT = 8899
IMAGE = "python:3.12-slim"


def _check_docker_available() -> bool:
    """Daemon reachable and the agent image present (mirrors the sibling module)."""
    import shutil
    import subprocess

    if not shutil.which("docker"):
        return False
    try:
        if (
            subprocess.run(  # noqa: S603
                ["docker", "info", "--format", "{{.OSType}}"], capture_output=True, text=True, timeout=5, check=True
            ).stdout.strip()
            != "linux"
        ):
            return False
        return (
            subprocess.run(  # noqa: S603
                ["docker", "image", "inspect", IMAGE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
            ).returncode
            == 0
        )
    except Exception:
        return False


docker_skip = pytest.mark.skipif(
    not _check_docker_available(), reason="Linux Docker daemon or python:3.12-slim unavailable"
)


async def _docker_client():
    import aiodocker

    return aiodocker.Docker()


async def _suppress(coro: Any) -> None:
    with contextlib.suppress(Exception):
        await coro


async def _delete_volume(client: Any, name: str) -> None:
    """Best-effort volume removal (delete rides the Volume object)."""
    with contextlib.suppress(Exception):
        volume = await client.volumes.get(name)
        await volume.delete()


async def _delete_network(client: Any, name: str) -> None:
    """Best-effort network removal (delete rides the Network object)."""
    with contextlib.suppress(Exception):
        network = await client.networks.get(name)
        await network.delete()


async def _receiver_hits(client: Any, receiver: Any) -> dict[str, int]:
    """Count the receiver's logged hits per probe path.

    One-shot raw log fetch (the streaming helper forces follow=True and
    never returns; the JSON helper refuses the multiplexed content
    type). Frame-header bytes stay harmlessly in the text.
    """
    async with client._query(
        f"containers/{receiver._id}/logs",
        params={"stdout": True, "stderr": True, "follow": False},
    ) as response:
        text = await response.text()
    return {path: text.count(f"GET {path}") for path in ("/probe-domain", "/probe-ip")}


async def _probe(env: DockerEnvironment, url: str) -> bool:
    """Run one HTTP probe inside the agent container; True when it lands.

    Any completed HTTP exchange counts as landed (``http.server`` answers
    unknown paths with 404, which urllib raises as ``HTTPError``); only
    DNS, connection and timeout failures mean the probe did not reach.
    """
    result = await env.exec_shell(
        [
            "python",
            "-c",
            (
                "import sys,urllib.request,urllib.error\n"
                "opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))\n"
                "try:\n"
                "    opener.open(sys.argv[1], timeout=6).close()\n"
                "except urllib.error.HTTPError:\n"
                "    pass\n"
                "except (urllib.error.URLError, TimeoutError):\n"
                "    raise SystemExit(42)\n"
            ),
            url,
        ],
        timeout=30,
    )
    assert result.exit_code in {0, 42}, result.stderr.decode(errors="replace")
    return result.exit_code == 0


async def _probe_until_landed(env: DockerEnvironment, url: str, attempts: int = 3) -> bool:
    """Probe with retries for the loose policy (transient bridge hiccups).

    Only the allow_all phase retries; the deny_all phase probes once, so
    a retry can never mask a real isolation failure.
    """
    import asyncio

    for index in range(attempts):
        if await _probe(env, url):
            return True
        if index + 1 < attempts:
            await asyncio.sleep(1)
    return False


@docker_skip
@pytest.mark.timeout(300)
async def test_deny_all_blocks_domain_and_ip_probes_to_the_receiver(monkeypatch: pytest.MonkeyPatch) -> None:
    """allow_all probes land on the receiver; deny_all probes cannot reach it."""
    suffix = uuid.uuid4().hex[:8]
    net_name = f"hecate-egress-{suffix}"
    receiver_name = f"egress-receiver-{suffix}"
    agent = f"egress-{suffix}"

    import aiodocker

    client = aiodocker.Docker()
    monkeypatch.setattr("hecate.core.config.settings.AGENT_ENV_NETWORK_POLICY", "allow_all")
    monkeypatch.setattr("hecate.core.config.settings.DOCKER_NETWORK_MODE", net_name)
    monkeypatch.setattr("hecate.core.config.settings.DOCKER_AGENT_IMAGE", IMAGE)
    manager = EnvironmentManager(backend="docker")
    receiver: Any = None
    try:
        await client.networks.create({"Name": net_name, "Driver": "bridge"})
        receiver = await client.containers.create_or_replace(
            name=receiver_name,
            config={
                "Image": IMAGE,
                "Cmd": ["python", "-u", "-m", "http.server", str(RECEIVER_PORT), "--bind", "0.0.0.0"],  # noqa: S104 - must accept the bridge subnet
                "HostConfig": {"NetworkMode": net_name},
            },
        )
        await receiver.start()

        allow_env = await manager.get_or_create(agent)
        assert isinstance(allow_env, DockerEnvironment)
        await allow_env.write_file("files/keep.txt", b"survives network tightening")
        receiver_info = await receiver.show()
        receiver_ip = receiver_info["NetworkSettings"]["Networks"][net_name]["IPAddress"]
        assert receiver_ip, "receiver never received an address on the test network"
        domain_landed = await _probe_until_landed(allow_env, f"http://{receiver_name}:{RECEIVER_PORT}/probe-domain")
        ip_landed = await _probe_until_landed(allow_env, f"http://{receiver_ip}:{RECEIVER_PORT}/probe-ip")
        hits_after_allow = await _receiver_hits(client, receiver)
        assert domain_landed and ip_landed, "loose-policy probes must reach the receiver"
        assert hits_after_allow["/probe-domain"] == 1 and hits_after_allow["/probe-ip"] == 1

        monkeypatch.setattr("hecate.core.config.settings.AGENT_ENV_NETWORK_POLICY", "deny_all")
        monkeypatch.setattr("hecate.core.config.settings.AGENT_ENV_CREDENTIAL_SCOPING", True)
        deny_env = await manager.get_or_create(agent)
        assert isinstance(deny_env, DockerEnvironment)
        assert deny_env is not allow_env
        with pytest.raises(RuntimeError, match="retired"):
            await allow_env.exec_shell(["python", "--version"])
        assert await deny_env.read_file("files/keep.txt") == b"survives network tightening"
        info = await deny_env._container.show()
        assert info["HostConfig"]["NetworkMode"] == "none"
        assert set(info["NetworkSettings"]["Networks"]) == {"none"}

        domain_blocked = await _probe(deny_env, f"http://{receiver_name}:{RECEIVER_PORT}/probe-domain")
        ip_blocked = await _probe(deny_env, f"http://{receiver_ip}:{RECEIVER_PORT}/probe-ip")
        hits_after_deny = await _receiver_hits(client, receiver)

        assert not domain_blocked, "domain probe must not reach the receiver"
        assert not ip_blocked, "direct-IP probe must not reach the receiver"
        assert hits_after_deny == hits_after_allow, "receiver must record no further hits under deny_all"
        print(f"receiver hits with allow_all: {hits_after_allow}; with deny_all: {hits_after_deny}")
    finally:
        await manager.close_all()
        if receiver is not None:
            await _suppress(receiver.delete(force=True))
        await _delete_network(client, net_name)
        await _delete_volume(client, f"agent-{agent}")
        await _suppress(client.close())


@docker_skip
@pytest.mark.timeout(240)
async def test_credential_scoping_hides_synthetic_secrets_and_keeps_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scoped exec hides synthetic host secrets; normal commands and file I/O work."""
    suffix = uuid.uuid4().hex[:8]
    agent = f"cred-scope-{suffix}"
    env = DockerEnvironment(
        agent, image=IMAGE, network_mode="none", runtime="runc", credential_scope=CredentialScope(enabled=True)
    )
    client = await _docker_client()
    stale = None
    try:
        await client.volumes.create({"Name": f"agent-{agent}"})
        stale = await client.containers.create(
            {
                "Image": IMAGE,
                "Cmd": ["sleep", "infinity"],
                "Env": ["CONTAINER_OVERRIDE=synthetic-stale-content"],
                "HostConfig": {
                    "NetworkMode": "none",
                    "Runtime": "runc",
                    "Mounts": [{"Type": "volume", "Source": f"agent-{agent}", "Target": "/env"}],
                },
            },
            name=f"hecate-agent-{agent}",
        )
        await stale.start()
        # The container must use its own PATH even with an incompatible host value.
        monkeypatch.setenv("PATH", "/host-only-canary")
        monkeypatch.setenv("HECATE_SECRET_CANARY_TOKEN", "super-secret-canary")
        monkeypatch.setenv("DB_PASSWORD", "super-secret-password")
        monkeypatch.setenv("HECATE_DATABASE_URL", "postgresql://test:unmarked-passphrase@invalid-db/test")

        result = await env.exec_shell(["sh", "-c", "printenv"], timeout=30)
        assert result.exit_code == 0
        env_text = result.stdout.decode(errors="replace")
        assert "super-secret-canary" not in env_text
        assert "super-secret-password" not in env_text
        assert "HECATE_SECRET_CANARY_TOKEN" not in env_text
        assert "DB_PASSWORD" not in env_text
        assert "HECATE_DATABASE_URL" not in env_text
        assert "CONTAINER_OVERRIDE" not in env_text
        assert env._container._id != stale._id
        assert "/host-only-canary" not in env_text
        assert (await env.exec_shell(["python", "--version"])).exit_code == 0

        normal = await env.exec_shell(["echo", "io-ok"], timeout=30)
        assert normal.exit_code == 0 and b"io-ok" in normal.stdout

        await env.write_file("files/canary.txt", b"volume content")
        assert await env.read_file("files/canary.txt") == b"volume content"
    finally:
        await _suppress(env.remove())
        if stale is not None:
            await _suppress(stale.delete(force=True))
        await _delete_volume(client, f"agent-{agent}")
        await _suppress(client.close())


@docker_skip
@pytest.mark.timeout(240)
async def test_replacing_a_policy_mismatching_container_keeps_the_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale allow_all container is replaced; the /env volume survives."""
    suffix = uuid.uuid4().hex[:8]
    agent = f"replace-vol-{suffix}"
    monkeypatch.setattr("hecate.core.config.settings.AGENT_ENV_NETWORK_POLICY", "allow_all")
    env_before = DockerEnvironment(agent, network_mode="bridge")
    env_after: DockerEnvironment | None = None
    client = await _docker_client()
    try:
        await env_before.ensure_dirs()
        await env_before.write_file("files/keep.txt", b"survives replacement")

        monkeypatch.setattr("hecate.core.config.settings.AGENT_ENV_NETWORK_POLICY", "deny_all")
        env_after = DockerEnvironment(agent, network_policy=NetworkEgressPolicy(mode=NetworkPolicyMode.DENY_ALL))
        await env_after.ensure_dirs()

        assert env_after._container is not env_before._container
        info = await env_after._container.show()
        (attached,) = (info.get("NetworkSettings") or {}).get("Networks") or {}
        assert attached == "none"
        assert await env_after.read_file("files/keep.txt") == b"survives replacement"
    finally:
        await _suppress(env_before.remove())
        if env_after is not None:
            await _suppress(env_after.remove())
        await _delete_volume(client, f"agent-{agent}")
        await _suppress(client.close())


@docker_skip
@pytest.mark.timeout(240)
async def test_manager_replaces_cached_container_after_policy_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hot cache honors a tightened policy end to end on the real daemon."""
    suffix = uuid.uuid4().hex[:8]
    agent = f"mgr-tighten-{suffix}"
    monkeypatch.setattr("hecate.core.config.settings.AGENT_ENV_BACKEND", "docker")
    monkeypatch.setattr("hecate.core.config.settings.DOCKER_NETWORK_MODE", "bridge")
    monkeypatch.setattr("hecate.core.config.settings.AGENT_ENV_NETWORK_POLICY", "allow_all")
    manager = EnvironmentManager()
    client = await _docker_client()
    try:
        env_before = await manager.get_or_create(agent)
        assert isinstance(env_before, DockerEnvironment)
        await env_before.write_file("files/through-cache.txt", b"still there")
        old_container_id = env_before._container._id

        monkeypatch.setattr("hecate.core.config.settings.AGENT_ENV_NETWORK_POLICY", "deny_all")
        env_after = await manager.get_or_create(agent)
        assert isinstance(env_after, DockerEnvironment)
        assert env_after is not env_before
        assert env_after._container._id != old_container_id
        info = await env_after._container.show()
        assert set(info["NetworkSettings"]["Networks"]) == {"none"}
        assert await env_after.read_file("files/through-cache.txt") == b"still there"
    finally:
        await manager.close_all()
        await _delete_volume(client, f"agent-{agent}")
        await _suppress(client.close())


@docker_skip
@pytest.mark.timeout(240)
async def test_restart_replaces_container_on_same_name_non_internal_network() -> None:
    """Legacy network names confer no authority, including on a real daemon."""
    agent = f"same-name-{uuid.uuid4().hex[:8]}"
    network_name = f"hecate-internal-{agent[:12]}"
    client = await _docker_client()
    loose = DockerEnvironment(agent, network_mode=network_name, image=IMAGE)
    strict = DockerEnvironment(agent, image=IMAGE, network_policy=NetworkEgressPolicy(mode=NetworkPolicyMode.DENY_ALL))
    try:
        await client.networks.create({"Name": network_name, "Driver": "bridge"})
        await loose.ensure_dirs()
        await loose.write_file("files/legacy.txt", b"legacy volume is retained")
        before = await loose._container.show()
        await strict.ensure_dirs()
        after = await strict._container.show()
        network = await client.networks.get(network_name)
        assert before["Id"] != after["Id"]
        assert set(after["NetworkSettings"]["Networks"]) == {"none"}
        assert (await network.show())["Internal"] is False
        assert await strict.read_file("files/legacy.txt") == b"legacy volume is retained"
    finally:
        await strict.remove()
        await loose.remove()
        await _delete_network(client, network_name)
        await _delete_volume(client, f"agent-{agent}")
        await client.close()
