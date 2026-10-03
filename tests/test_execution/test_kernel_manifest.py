"""Kernel manifest validation tests (hecate-runner-preview task 1.2).

The manifest module now lives in the ``hecate-runtime`` kernel
(``hecate_runtime.manifest``); these tests cover the failure paths the
runner's profile gate consumes — digest mismatch, disallowed tool
permission, missing entry file — directly against the kernel import,
without the main-application compatibility shim.
"""

from __future__ import annotations

import io
import tarfile

import pytest
from hecate_runtime.manifest import (
    MANIFEST_VERSION,
    ManifestError,
    sha256_hex,
    validate_manifest_dict,
)

FILE_CONTENTS: dict[str, bytes] = {
    "agents/summary/main.json": b'{"kind": "entry"}',
    "agents/summary/graph.json": b'{"nodes": ["solo"]}',
}


def _manifest_dict(tools: list[dict] | None = None, entry: str = "agents/summary/main.json") -> dict:
    data: dict = {
        "manifest_version": MANIFEST_VERSION,
        "contract_version": "0.1",
        "backend_type": "pregel",
        "backend_compat_version": "0.1",
        "entry": entry,
        "files": [
            {"path": path, "sha256": sha256_hex(content), "size": len(content)}
            for path, content in FILE_CONTENTS.items()
        ],
    }
    if tools is not None:
        data["tools"] = tools
    return data


def _archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path, content in FILE_CONTENTS.items():
            info = tarfile.TarInfo(name=path)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def test_malformed_digest_rejected() -> None:
    data = _manifest_dict()
    data["files"][0]["sha256"] = "ZZZ-not-hex"
    with pytest.raises(ManifestError, match="files\\[0\\].sha256"):
        validate_manifest_dict(data)


def test_digest_of_real_content_mismatch_rejected_in_archive() -> None:
    from hecate_runtime.manifest import verify_archive

    data = _manifest_dict()
    data["files"][1]["sha256"] = sha256_hex(b"tampered")
    with pytest.raises(ManifestError, match="sha256 mismatch for"):
        verify_archive(data, _archive())


def test_write_tool_permission_is_structurally_valid() -> None:
    """write/approval_required are legal manifest values; the runner's
    preview gate (not the shared validator) rejects them at profile load."""
    manifest = validate_manifest_dict(
        _manifest_dict(tools=[{"name": "update_record", "schema_ref": "schemas/update.json", "permission": "write"}])
    )
    assert manifest.tools[0].permission == "write"


def test_invalid_permission_rejected() -> None:
    with pytest.raises(ManifestError, match="permission"):
        validate_manifest_dict(_manifest_dict(tools=[{"name": "t", "schema_ref": "s.json", "permission": "admin"}]))


def test_entry_must_name_listed_file() -> None:
    with pytest.raises(ManifestError, match="entry must name a listed file"):
        validate_manifest_dict(_manifest_dict(entry="agents/summary/absent.json"))


def test_credential_like_key_rejected() -> None:
    data = _manifest_dict()
    data["backend_ns"] = {"api_key": "should-not-be-here"}
    with pytest.raises(ManifestError, match="forbidden credential-like key"):
        validate_manifest_dict(data)
