"""Shared fixtures/helpers for runner package tests."""

from __future__ import annotations

import json
from pathlib import Path

from hecate_runtime.manifest import sha256_hex

ENTRY_NAME = "agents/summary/main.json"
ENTRY_CONTENT = b'{"kind": "entry", "tools": ["query_inventory"]}'
READER_TOKEN = "reader-secret-token"


def write_profile(tmp_path: Path, *, allowlist: list[str] | None = None) -> Path:
    """Materialize a valid preview profile; return its directory."""

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
    (profile / "secrets/app-reader-token").write_text(READER_TOKEN, encoding="utf-8")
    return profile
