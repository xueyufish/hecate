"""Managed profile parsing and CLI assembly gate (step6a).

The managed overlay is opt-in: a ``control_plane`` block plus the durable
profile assembles the closed loop, a ``control_plane`` without durable
storage is refused at startup (the receive queue IS the persistent task
ledger), and a profile without ``control_plane`` parses exactly as before.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from hecate_runner.__main__ import main
from hecate_runner.profile import ProfileError, load_profile
from hecate_runtime.manifest import sha256_hex

ENTRY_NAME = "agents/summary/main.json"
ENTRY_CONTENT = b'{"kind": "entry", "tools": ["query_inventory"]}'


def _write_profile(
    tmp_path: Path,
    *,
    control_plane: dict | None = None,
    durable: bool = True,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> Path:
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
        "tools": [],
    }
    (profile / "agent-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    config: dict = {
        "host": "127.0.0.1",
        "port": 0,
        "evidence_dir": "evidence",
        "model": {"backend": "stub"},
        "tool_allowlist": ["query_inventory"],
        "shutdown_token_ref": "env:RUNNER_SHUTDOWN_TOKEN",
    }
    if durable:
        config["durable"] = {"database_url": f"sqlite:///{(tmp_path / 'host.db').as_posix()}", "workspace": "w"}
    if control_plane is not None:
        config["control_plane"] = control_plane
    (profile / "runner.json").write_text(json.dumps(config), encoding="utf-8")

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
    (profile / "secrets/app-reader-token").write_text("reader-token", encoding="utf-8")
    (profile / "secrets/managed-secret").write_text("managed-material", encoding="utf-8")
    return profile


def _control_plane(**overrides: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "base_url": "http://platform.test",
        "workspace_id": "00000000-0000-0000-0000-0000000000aa",
        "trust_root": "root-a",
        "host_id": "host-a",
        "issuer_domain": "issuer-a",
        "secret_ref": "file:secrets/managed-secret",
        "data_domains": ["domain_a"],
    }
    block.update(overrides)
    return {key: value for key, value in block.items() if value is not _REMOVE}


class _Remove:
    pass


_REMOVE = _Remove()


def test_control_plane_without_durable_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "tok")
    profile_dir = _write_profile(tmp_path, control_plane=_control_plane(), durable=False)
    import contextlib
    import io

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = main(["--profile", str(profile_dir), "--business-api", "http://127.0.0.1:1"])
    assert code == 2
    assert "durable" in err.getvalue()


def test_control_plane_parses_domains_and_issuer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "tok")
    profile_dir = _write_profile(tmp_path, control_plane=_control_plane())
    profile = load_profile(profile_dir)
    cp = profile.config.control_plane
    assert cp is not None
    assert cp.data_domains == ("domain_a",)
    assert cp.issuer_domain == "issuer-a"


def test_control_plane_renewal_window_default_and_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The declared renewal/disconnect window defaults to the historical 5 s
    budget and accepts an explicit operator override (step6b)."""
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "tok")
    profile = load_profile(_write_profile(tmp_path, control_plane=_control_plane()))
    assert profile.config.control_plane is not None
    assert profile.config.control_plane.lease_refresh_wait_seconds == 5.0

    overridden = load_profile(_write_profile(tmp_path, control_plane=_control_plane(lease_refresh_wait_seconds=12)))
    assert overridden.config.control_plane is not None
    assert overridden.config.control_plane.lease_refresh_wait_seconds == 12.0


def test_control_plane_renewal_window_must_be_positive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "tok")
    profile_dir = _write_profile(tmp_path, control_plane=_control_plane(lease_refresh_wait_seconds=0))
    with pytest.raises(ProfileError, match="lease_refresh_wait_seconds"):
        load_profile(profile_dir)


def test_control_plane_absent_keeps_standalone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "tok")
    profile = load_profile(_write_profile(tmp_path))
    assert profile.config.control_plane is None


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"issuer_domain": ""}, "issuer_domain"),
        ({"issuer_domain": _REMOVE}, "issuer_domain"),
        ({"data_domains": ["a", "a"]}, "duplicate"),
        ({"data_domains": ["a", ""]}, "non-empty"),
        ({"data_domains": "domain_a"}, "array"),
    ],
)
def test_control_plane_invalid_fields_fail_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: dict, message: str
) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "tok")
    profile_dir = _write_profile(tmp_path, control_plane=_control_plane(**override))
    with pytest.raises(ProfileError, match=message):
        load_profile(profile_dir)
