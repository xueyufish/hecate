"""Artifact manifest mapping and validation (pure functions).

A manifest describes one standard archive (tar.gz) holding an execution
definition. Integrity is per-file sha256; the manifest never carries plaintext
credentials, business data, or online platform ID lookup prerequisites;
backend-specific graphs/scripts live in ``backend_ns``. There are no
installer-script fields and loaders MUST NOT execute installation scripts.
"""

from __future__ import annotations

import hashlib
import io
import re
import tarfile
from dataclasses import dataclass, field
from typing import Any

MANIFEST_VERSION = "1"

_FORBIDDEN_KEY_PATTERN = re.compile(
    r"credential|secret|password|passwd|api_?key|private_?key|access_?token",
    re.IGNORECASE,
)


def _escapes_archive(path: str) -> bool:
    """True for absolute paths, ``..`` segments, or empty paths."""

    return not path or path.startswith("/") or any(segment == ".." for segment in path.split("/"))


class ManifestError(ValueError):
    """Manifest structure, integrity, or content-policy violation."""


@dataclass(frozen=True)
class ManifestFile:
    """One archived file with its digest."""

    path: str
    sha256: str
    size: int

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size}


@dataclass(frozen=True)
class ManifestTool:
    """One tool declared by the artifact."""

    name: str
    schema_ref: str
    permission: str  # "read" | "write" | "approval_required"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "schema_ref": self.schema_ref, "permission": self.permission}


@dataclass(frozen=True)
class ArtifactManifest:
    """Execution artifact manifest (schema: artifact-manifest.schema.json)."""

    contract_version: str
    backend_type: str
    backend_compat_version: str
    entry: str
    files: tuple[ManifestFile, ...]
    required_capabilities: tuple[str, ...] = ()
    tools: tuple[ManifestTool, ...] = ()
    model_config_ref: str | None = None
    component_config_refs: tuple[str, ...] = ()
    artifact_schema_ref: str | None = None
    evidence_refs: tuple[str, ...] = ()
    backend_ns: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "manifest_version": MANIFEST_VERSION,
            "contract_version": self.contract_version,
            "backend_type": self.backend_type,
            "backend_compat_version": self.backend_compat_version,
            "entry": self.entry,
            "files": [f.to_dict() for f in self.files],
        }
        if self.required_capabilities:
            out["required_capabilities"] = list(self.required_capabilities)
        if self.tools:
            out["tools"] = [t.to_dict() for t in self.tools]
        if self.model_config_ref is not None:
            out["model_config_ref"] = self.model_config_ref
        if self.component_config_refs:
            out["component_config_refs"] = list(self.component_config_refs)
        if self.artifact_schema_ref is not None:
            out["artifact_schema_ref"] = self.artifact_schema_ref
        if self.evidence_refs:
            out["evidence_refs"] = list(self.evidence_refs)
        if self.backend_ns:
            out["backend_ns"] = self.backend_ns
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactManifest:
        return cls(
            contract_version=data["contract_version"],
            backend_type=data["backend_type"],
            backend_compat_version=data["backend_compat_version"],
            entry=data["entry"],
            files=tuple(ManifestFile(path=f["path"], sha256=f["sha256"], size=f["size"]) for f in data["files"]),
            required_capabilities=tuple(data.get("required_capabilities", ())),
            tools=tuple(
                ManifestTool(name=t["name"], schema_ref=t["schema_ref"], permission=t["permission"])
                for t in data.get("tools", ())
            ),
            model_config_ref=data.get("model_config_ref"),
            component_config_refs=tuple(data.get("component_config_refs", ())),
            artifact_schema_ref=data.get("artifact_schema_ref"),
            evidence_refs=tuple(data.get("evidence_refs", ())),
            backend_ns=data.get("backend_ns", {}),
        )


def _reject_forbidden_keys(node: Any, path: str, errors: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            key_str = str(key)
            if _FORBIDDEN_KEY_PATTERN.search(key_str):
                errors.append(f"forbidden credential-like key at {path}.{key_str}")
            else:
                _reject_forbidden_keys(value, f"{path}.{key_str}", errors)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _reject_forbidden_keys(item, f"{path}[{index}]", errors)


def validate_manifest_dict(data: dict[str, Any]) -> ArtifactManifest:
    """Validate manifest structure and content policy; return the mapping.

    Raises :class:`ManifestError` on any violation. Callers that also have the
    archive bytes should follow up with :func:`verify_archive`.
    """

    errors: list[str] = []

    required = ["contract_version", "backend_type", "backend_compat_version", "entry", "files"]
    for key in required:
        if key not in data:
            errors.append(f"missing required field: {key}")
    if errors:
        raise ManifestError("; ".join(errors))

    if data.get("manifest_version") != MANIFEST_VERSION:
        errors.append(f"manifest_version must be {MANIFEST_VERSION!r}")
    for key in ("contract_version", "backend_type", "backend_compat_version", "entry"):
        value = data.get(key)
        if not isinstance(value, str) or not value:
            errors.append(f"{key} must be a non-empty string")
    if isinstance(data.get("entry"), str) and _escapes_archive(data["entry"]):
        errors.append(f"entry path escapes the archive: {data['entry']!r}")

    files = data.get("files")
    if not isinstance(files, list) or not files:
        errors.append("files must be a non-empty array")
    else:
        seen_paths: set[str] = set()
        for index, item in enumerate(files):
            if not isinstance(item, dict):
                errors.append(f"files[{index}] must be an object")
                continue
            path_value = item.get("path")
            if not isinstance(path_value, str) or not path_value:
                errors.append(f"files[{index}].path must be a non-empty string")
            elif _escapes_archive(path_value):
                errors.append(f"files[{index}].path escapes the archive: {path_value!r}")
            elif path_value in seen_paths:
                errors.append(f"duplicate file path: {path_value!r}")
            else:
                seen_paths.add(path_value)
            digest = item.get("sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                errors.append(f"files[{index}].sha256 must be a lowercase hex sha256")
            size = item.get("size")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                errors.append(f"files[{index}].size must be a non-negative integer")

    for tool in data.get("tools", []) or []:
        if isinstance(tool, dict) and tool.get("permission") not in {"read", "write", "approval_required"}:
            errors.append(f"tool {tool.get('name')!r} permission must be read/write/approval_required")

    _reject_forbidden_keys(data, "manifest", errors)

    if errors:
        raise ManifestError("; ".join(errors))
    return ArtifactManifest.from_dict(data)


def verify_archive(data: dict[str, Any], archive: bytes) -> ArtifactManifest:
    """Validate the manifest, then verify the archive bytes against it.

    Every file listed in the manifest must be present in the tar with a
    matching sha256 and size; any unlisted member is a violation.
    """

    manifest = validate_manifest_dict(data)
    listed_paths = {entry.path for entry in manifest.files}

    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            member_names = {m.name for m in tar.getmembers() if m.isfile()}
            for entry in manifest.files:
                if entry.path not in member_names:
                    raise ManifestError(f"missing archive member: {entry.path!r}")
                extracted = tar.extractfile(entry.path)
                if extracted is None:
                    raise ManifestError(f"missing archive member: {entry.path!r}")
                content = extracted.read()
                digest = hashlib.sha256(content).hexdigest()
                if digest != entry.sha256:
                    raise ManifestError(f"sha256 mismatch for {entry.path!r}")
                if len(content) != entry.size:
                    raise ManifestError(
                        f"size mismatch for {entry.path!r}: manifest {entry.size}, archive {len(content)}"
                    )
            for member in member_names:
                if member not in listed_paths:
                    raise ManifestError(f"unlisted archive member: {member!r}")
    except tarfile.TarError as exc:
        raise ManifestError(f"unreadable archive: {exc}") from exc

    return manifest


def sha256_hex(content: bytes) -> str:
    """Helper for building manifests (test and tooling use)."""

    return hashlib.sha256(content).hexdigest()
