"""Profile loading and preview-gate tests (hecate-runner-preview tasks 2.2/2.3).

Covers the fail-fast paths the standalone-runner-host spec requires:
missing artifacts, digest mismatch, unresolvable secret references,
write-tool rejection, allowlist violations, and read_only-only roles.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hecate_runner.profile import ProfileError, load_profile, resolve_identity
from hecate_runtime.manifest import sha256_hex

ENTRY_NAME = "agents/summary/main.json"
ENTRY_CONTENT = b'{"kind": "entry", "tools": ["query_inventory"]}'


def _write_profile(tmp_path: Path, *, tools: list[dict] | None = None, allowlist: list[str] | None = None) -> Path:
    profile = tmp_path / "profile"
    (profile / "files" / "agents/summary").mkdir(parents=True, exist_ok=True)
    (profile / "files" / ENTRY_NAME).write_bytes(ENTRY_CONTENT)

    manifest = {
        "manifest_version": "1",
        "contract_version": "0.1",
        "backend_type": "pregel",
        "backend_compat_version": "0.1",
        "entry": ENTRY_NAME,
        "files": [{"path": ENTRY_NAME, "sha256": sha256_hex(ENTRY_CONTENT), "size": len(ENTRY_CONTENT)}],
    }
    if tools is not None:
        manifest["tools"] = tools
    (profile / "agent-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    (profile / "runner.json").write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": 0,
                "evidence_dir": "evidence",
                "model": {"backend": "stub"},
                "tool_allowlist": allowlist if allowlist is not None else ["query_inventory"],
                "shutdown_token_ref": "env:RUNNER_SHUTDOWN_TOKEN",
            }
        ),
        encoding="utf-8",
    )

    (profile / "identity.json").write_text(
        json.dumps(
            {
                "identities": [
                    {
                        "principal": "app-reader",
                        "role": "read_only",
                        "domains": ["domain_a"],
                        "credential": "file:secrets/app-reader-token",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (profile / "secrets").mkdir(exist_ok=True)
    (profile / "secrets/app-reader-token").write_text("reader-secret-token", encoding="utf-8")
    return profile


@pytest.fixture()
def _shutdown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown-token-value")


def test_valid_profile_loads(tmp_path: Path, _shutdown_env: None) -> None:
    profile = load_profile(_write_profile(tmp_path))
    assert profile.manifest.entry == ENTRY_NAME
    assert profile.config.tool_allowlist == ("query_inventory",)
    assert profile.identities[0].principal == "app-reader"
    assert profile.entry_content == ENTRY_CONTENT


def test_missing_manifest_fails_fast(tmp_path: Path, _shutdown_env: None) -> None:
    profile_dir = _write_profile(tmp_path)
    (profile_dir / "agent-manifest.json").unlink()
    with pytest.raises(ProfileError, match="agent manifest"):
        load_profile(profile_dir)


def test_digest_mismatch_fails_startup(tmp_path: Path, _shutdown_env: None) -> None:
    profile_dir = _write_profile(tmp_path)
    (profile_dir / "files" / ENTRY_NAME).write_bytes(b"tampered")
    with pytest.raises(ProfileError, match="digest mismatch"):
        load_profile(profile_dir)


def test_missing_trust_material_fails_startup(tmp_path: Path, _shutdown_env: None) -> None:
    profile_dir = _write_profile(tmp_path)
    (profile_dir / "identity.json").unlink()
    with pytest.raises(ProfileError, match="identity trust material"):
        load_profile(profile_dir)


def test_unresolved_secret_reference_fails_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile_dir = _write_profile(tmp_path)
    monkeypatch.delenv("RUNNER_SHUTDOWN_TOKEN", raising=False)
    with pytest.raises(ProfileError, match="RUNNER_SHUTDOWN_TOKEN"):
        load_profile(profile_dir)


def test_write_tool_in_manifest_fails_startup_naming_tool(tmp_path: Path, _shutdown_env: None) -> None:
    profile_dir = _write_profile(
        tmp_path,
        tools=[{"name": "update_record", "schema_ref": "schemas/update.json", "permission": "write"}],
    )
    with pytest.raises(ProfileError, match="update_record.*write"):
        load_profile(profile_dir)


def test_approval_tool_in_manifest_fails_startup(tmp_path: Path, _shutdown_env: None) -> None:
    profile_dir = _write_profile(
        tmp_path,
        tools=[{"name": "submit_ticket", "schema_ref": "schemas/ticket.json", "permission": "approval_required"}],
    )
    with pytest.raises(ProfileError, match="submit_ticket"):
        load_profile(profile_dir)


def test_allowlist_beyond_manifest_read_tools_fails(tmp_path: Path, _shutdown_env: None) -> None:
    profile_dir = _write_profile(
        tmp_path,
        tools=[{"name": "query_inventory", "schema_ref": "schemas/q.json", "permission": "read"}],
        allowlist=["query_inventory", "read_docs"],
    )
    with pytest.raises(ProfileError, match="read_docs"):
        load_profile(profile_dir)


def test_non_read_only_role_rejected(tmp_path: Path) -> None:
    profile_dir = _write_profile(tmp_path)
    data = json.loads((profile_dir / "identity.json").read_text(encoding="utf-8"))
    data["identities"][0]["role"] = "admin"
    (profile_dir / "identity.json").write_text(json.dumps(data), encoding="utf-8")
    import os

    os.environ.setdefault("RUNNER_SHUTDOWN_TOKEN", "shutdown-token-value")
    try:
        with pytest.raises(ProfileError, match="read_only"):
            load_profile(profile_dir)
    finally:
        os.environ.pop("RUNNER_SHUTDOWN_TOKEN", None)


def test_resolve_identity_constant_time_lookup(tmp_path: Path, _shutdown_env: None) -> None:
    profile = load_profile(_write_profile(tmp_path))
    assert resolve_identity(profile.identities, "reader-secret-token") is not None
    assert resolve_identity(profile.identities, "wrong-token") is None
    assert resolve_identity(profile.identities, "") is None


@pytest.mark.parametrize(
    "field, value",
    [
        ("backend_type", "foreign-runtime"),
        ("backend_compat_version", "99"),
        ("contract_version", "99"),
        ("required_capabilities", ["long_task_recovery"]),
    ],
)
def test_unsupported_manifest_fails_at_startup(tmp_path, _shutdown_env, field, value):
    profile_dir = _write_profile(tmp_path)
    path = profile_dir / "agent-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest[field] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile(profile_dir)


@pytest.mark.parametrize(
    "field, value",
    [("host", "0.0.0.0"), ("max_concurrency", True), ("tool_allowlist", ["query_inventory", "query_inventory"])],  # noqa: S104
)
def test_invalid_preview_configuration_fails_at_startup(tmp_path, _shutdown_env, field, value):
    profile_dir = _write_profile(tmp_path)
    path = profile_dir / "runner.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config[field] = value
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile(profile_dir)


def test_empty_secret_fails_at_startup(tmp_path, _shutdown_env):
    profile_dir = _write_profile(tmp_path)
    (profile_dir / "secrets/app-reader-token").write_text("  ", encoding="utf-8")
    with pytest.raises(ProfileError, match="empty"):
        load_profile(profile_dir)


def test_declared_tool_requires_verified_schema(tmp_path, _shutdown_env):
    profile_dir = _write_profile(
        tmp_path,
        tools=[
            {
                "name": "query_inventory",
                "permission": "read",
                "schema_ref": "schemas/inventory.json",
            }
        ],
    )
    with pytest.raises(ProfileError, match="digest-verified"):
        load_profile(profile_dir)


def test_external_schema_refs_fail_startup_without_network(tmp_path, _shutdown_env):
    schema_name = "schemas/inventory.json"
    profile_dir = _write_profile(
        tmp_path,
        tools=[
            {
                "name": "query_inventory",
                "permission": "read",
                "schema_ref": schema_name,
            }
        ],
    )
    schema_content = b'{"$ref": "https://unexpected.example/schema"}'
    schema_path = profile_dir / "files" / schema_name
    schema_path.parent.mkdir()
    schema_path.write_bytes(schema_content)
    manifest_path = profile_dir / "agent-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append({"path": schema_name, "sha256": sha256_hex(schema_content), "size": len(schema_content)})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ProfileError, match="internal references only"):
        load_profile(profile_dir)
