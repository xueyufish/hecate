"""Shared fixtures/helpers for runner package tests."""

from __future__ import annotations

import json
from pathlib import Path

from hecate_runtime.manifest import sha256_hex

ENTRY_NAME = "agents/summary/main.json"
ENTRY_CONTENT = b'{"kind": "entry", "tools": ["query_inventory"]}'
WRITE_ENTRY_CONTENT = b'{"kind": "entry", "tools": ["query_inventory", "submit_inventory_update"]}'
APPROVAL_ENTRY_CONTENT = b'{"kind": "entry", "tools": ["query_inventory", "submit_inventory_update", "submit_ticket"]}'
READER_TOKEN = "reader-secret-token"

WRITE_TOOL_SCHEMA = {
    "type": "object",
    "required": ["domain", "sku", "quantity"],
    "properties": {
        "domain": {"type": "string", "minLength": 1},
        "sku": {"type": "string", "minLength": 1},
        "quantity": {"type": "integer", "minimum": 1},
    },
}


def write_profile(
    tmp_path: Path,
    *,
    allowlist: list[str] | None = None,
    durable: dict | None = None,
    evidence: dict | None = None,
    with_write_tool: bool = False,
    with_approval_tool: bool = False,
) -> Path:
    """Materialize a valid profile; return its directory.

    ``durable`` is merged into ``runner.json`` as the ``durable`` block
    (persistent task/action ledger); ``with_write_tool`` declares the
    builtin ``submit_inventory_update`` write tool, which only the durable
    profile admits; ``with_approval_tool`` declares a manifest-defined
    approval-required tool (durable waiting, step6c).
    """

    tools = allowlist if allowlist is not None else ["query_inventory"]
    # The preview entry must declare the configured tools in order, so it
    # is derived from the allowlist instead of a fixed shape.
    entry_content = json.dumps({"kind": "entry", "tools": tools}).encode()
    profile = tmp_path / "profile"
    (profile / "files" / "agents/summary").mkdir(parents=True, exist_ok=True)
    (profile / "files" / ENTRY_NAME).write_bytes(entry_content)

    files = [{"path": ENTRY_NAME, "sha256": sha256_hex(entry_content), "size": len(entry_content)}]
    manifest_tools: list[dict] = []
    if with_approval_tool:
        schema_name = "tools/submit_ticket.schema.json"
        schema_bytes = json.dumps(
            {
                "type": "object",
                "required": ["ticket"],
                "properties": {"ticket": {"type": "string", "minLength": 1}},
            }
        ).encode()
        (profile / "files" / "tools").mkdir(exist_ok=True)
        (profile / "files" / schema_name).write_bytes(schema_bytes)
        files.append({"path": schema_name, "sha256": sha256_hex(schema_bytes), "size": len(schema_bytes)})
        manifest_tools.append({"name": "submit_ticket", "schema_ref": schema_name, "permission": "approval_required"})
    if with_write_tool:
        schema_name = "tools/submit_inventory_update.schema.json"
        schema_bytes = json.dumps(WRITE_TOOL_SCHEMA).encode()
        (profile / "files" / "tools").mkdir(exist_ok=True)
        (profile / "files" / schema_name).write_bytes(schema_bytes)
        files.append({"path": schema_name, "sha256": sha256_hex(schema_bytes), "size": len(schema_bytes)})
        manifest_tools.append({"name": "submit_inventory_update", "schema_ref": schema_name, "permission": "write"})

    manifest = {
        "manifest_version": "1",
        "contract_version": "0.1",
        "backend_type": "pregel",
        "backend_compat_version": "0.1",
        "entry": ENTRY_NAME,
        "files": files,
        "tools": manifest_tools,
    }
    (profile / "agent-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    config: dict = {
        "host": "127.0.0.1",
        "port": 0,
        "evidence_dir": "evidence",
        "model": {"backend": "stub"},
        "tool_allowlist": tools,
        "shutdown_token_ref": "env:RUNNER_SHUTDOWN_TOKEN",
    }
    if durable is not None:
        config["durable"] = durable
    if evidence is not None:
        config["evidence"] = evidence
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
    (profile / "secrets/app-reader-token").write_text(READER_TOKEN, encoding="utf-8")
    return profile
