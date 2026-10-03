"""Profile loading and the read-only preview gate.

A profile directory holds three required files:

- ``agent-manifest.json`` — the kernel :class:`ArtifactManifest`; digests
  are verified against ``files/`` sibling content (or an accompanying
  archive via ``agent-artifact.tar.gz``).
- ``runner.json`` — host configuration: bind address, evidence directory,
  model settings, tool allowlist, shutdown token reference. Secrets are
  referenced (env var or file path), never inlined.
- ``identity.json`` — trust material mapping credentials (hashed) to
  server-side principals with ``read_only`` roles and data domains.

Any missing file, digest mismatch, unresolvable reference, or policy
violation fails startup with an error naming the offending item; the
runner never serves a partially loaded profile. The preview gate rejects
manifests declaring ``write`` or ``approval_required`` tools at startup.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path

from hecate_runtime.manifest import ArtifactManifest, ManifestError, sha256_hex, validate_manifest_dict

PREVIEW_ROLE = "read_only"
_PREVIEW_FORBIDDEN_PERMISSIONS = {"write", "approval_required"}


class ProfileError(Exception):
    """Profile structure, integrity, or policy violation; startup fails."""


@dataclass(frozen=True)
class RunnerConfig:
    """Validated host configuration from ``runner.json``."""

    host: str
    port: int
    evidence_dir: Path
    model_backend: str  # "stub" | "endpoint"
    model_endpoint: str | None
    model_auth_env: str | None  # env var name holding endpoint auth material
    tool_allowlist: tuple[str, ...]
    shutdown_token_ref: str  # env var name or "file:<path>"
    max_request_bytes: int
    max_concurrency: int


@dataclass(frozen=True)
class TrustedIdentity:
    """Server-side identity resolved from trust material."""

    principal: str
    role: str
    domains: tuple[str, ...]
    credential_sha256: str


def _resolve_secret_ref(ref: str, profile_dir: Path) -> str:
    """Resolve a secret reference (``env:NAME`` or ``file:relative/path``)."""

    if ref.startswith("env:"):
        name = ref[4:]
        value = os.environ.get(name)
        if not value:
            raise ProfileError(f"secret reference env:{name} is not set")
        return value
    if ref.startswith("file:"):
        path = profile_dir / ref[5:]
        if not path.is_file():
            raise ProfileError(f"secret reference file:{ref[5:]} does not exist")
        return path.read_text(encoding="utf-8").strip()
    raise ProfileError(f"secret reference must start with 'env:' or 'file:': {ref!r}")


def _load_json(path: Path, label: str) -> dict:
    if not path.is_file():
        raise ProfileError(f"missing profile file: {label} ({path.name})")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProfileError(f"unreadable {label}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError(f"{label} must be a JSON object")
    return data


def _load_manifest(profile_dir: Path) -> ArtifactManifest:
    raw = _load_json(profile_dir / "agent-manifest.json", "agent manifest")
    try:
        manifest = validate_manifest_dict(raw)
    except ManifestError as exc:
        raise ProfileError(f"invalid agent manifest: {exc}") from exc

    for entry in manifest.files:
        content_path = profile_dir / "files" / entry.path
        if content_path.is_file():
            content = content_path.read_bytes()
            if sha256_hex(content) != entry.sha256:
                raise ProfileError(f"manifest digest mismatch for {entry.path!r}")
            if len(content) != entry.size:
                raise ProfileError(f"manifest size mismatch for {entry.path!r}")
        else:
            raise ProfileError(f"manifest file missing from profile: files/{entry.path}")
    return manifest


def _load_config(profile_dir: Path) -> RunnerConfig:
    data = _load_json(profile_dir / "runner.json", "runner config")
    errors: list[str] = []

    host = data.get("host", "127.0.0.1")
    port = data.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not (0 <= port < 65536):
        errors.append("port must be an integer in [0, 65536) (0 = OS-assigned)")

    evidence_dir = data.get("evidence_dir")
    if not isinstance(evidence_dir, str) or not evidence_dir:
        errors.append("evidence_dir must be a non-empty string")

    model = data.get("model", {})
    if not isinstance(model, dict):
        errors.append("model must be an object")
        model = {}
    model_backend = model.get("backend", "stub")
    if model_backend not in {"stub", "endpoint"}:
        errors.append("model.backend must be 'stub' or 'endpoint'")
    model_endpoint = model.get("endpoint")
    model_auth_env = model.get("auth_env")
    if model_backend == "endpoint" and (not isinstance(model_endpoint, str) or not model_endpoint):
        errors.append("model.endpoint is required when backend is 'endpoint'")

    allowlist = data.get("tool_allowlist", [])
    if not isinstance(allowlist, list) or any(not isinstance(name, str) or not name for name in allowlist):
        errors.append("tool_allowlist must be an array of non-empty strings")

    shutdown_ref = data.get("shutdown_token_ref")
    if not isinstance(shutdown_ref, str) or not shutdown_ref:
        errors.append("shutdown_token_ref must be a non-empty secret reference")

    max_request_bytes = data.get("max_request_bytes", 1 << 20)
    if not isinstance(max_request_bytes, int) or isinstance(max_request_bytes, bool) or max_request_bytes <= 0:
        errors.append("max_request_bytes must be a positive integer")

    max_concurrency = data.get("max_concurrency", 1)
    if max_concurrency != 1:
        errors.append("max_concurrency must be 1 in the preview profile")

    if errors:
        raise ProfileError("invalid runner config: " + "; ".join(errors))

    # Validation above guarantees a non-empty string; narrow without assert
    # (ruff S101) for the type checker.
    evidence_dir_str = evidence_dir if isinstance(evidence_dir, str) else ""

    return RunnerConfig(
        host=host if isinstance(host, str) else "127.0.0.1",
        port=port,
        evidence_dir=profile_dir / evidence_dir_str,
        model_backend=model_backend,
        model_endpoint=model_endpoint if model_backend == "endpoint" else None,
        model_auth_env=model_auth_env if isinstance(model_auth_env, str) else None,
        tool_allowlist=tuple(allowlist),
        shutdown_token_ref=shutdown_ref,
        max_request_bytes=max_request_bytes,
        max_concurrency=1,
    )


def _load_identities(profile_dir: Path) -> tuple[TrustedIdentity, ...]:
    data = _load_json(profile_dir / "identity.json", "identity trust material")
    entries = data.get("identities")
    if not isinstance(entries, list) or not entries:
        raise ProfileError("identity.json must carry a non-empty 'identities' array")

    identities: list[TrustedIdentity] = []
    seen_credentials: set[str] = set()
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ProfileError(f"identities[{index}] must be an object")
        errors: list[str] = []
        principal = item.get("principal")
        if not isinstance(principal, str) or not principal:
            errors.append("principal must be a non-empty string")
        role = item.get("role")
        if role != PREVIEW_ROLE:
            errors.append(f"role must be {PREVIEW_ROLE!r} in the preview profile")
        domains = item.get("domains")
        if not isinstance(domains, list) or not domains or any(not isinstance(d, str) or not d for d in domains):
            errors.append("domains must be a non-empty array of strings")
        credential = item.get("credential")
        if not isinstance(credential, str) or not credential:
            errors.append("credential must be a non-empty secret reference")
        if errors:
            raise ProfileError(f"invalid identities[{index}]: " + "; ".join(errors))

        material = _resolve_secret_ref(credential, profile_dir)
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        if digest in seen_credentials:
            raise ProfileError(f"identities[{index}]: duplicate credential material")
        seen_credentials.add(digest)
        identities.append(
            TrustedIdentity(
                principal=principal,
                role=role,
                domains=tuple(domains),
                credential_sha256=digest,
            )
        )
    return tuple(identities)


def resolve_identity(identities: tuple[TrustedIdentity, ...], presented: str) -> TrustedIdentity | None:
    """Constant-time lookup of a presented credential against trust material."""

    digest = hashlib.sha256(presented.encode("utf-8")).hexdigest()
    for identity in identities:
        if hmac.compare_digest(identity.credential_sha256, digest):
            return identity
    return None


@dataclass(frozen=True)
class Profile:
    """A fully validated startup profile."""

    directory: Path
    manifest: ArtifactManifest
    config: RunnerConfig
    identities: tuple[TrustedIdentity, ...]
    shutdown_token: str
    entry_content: bytes

    def capabilities_summary(self) -> dict:
        return {
            "backend_type": self.manifest.backend_type,
            "backend_compat_version": self.manifest.backend_compat_version,
            "model_source": self.config.model_backend,
            "read_tools": sorted(self.config.tool_allowlist),
        }


def load_profile(profile_dir: Path) -> Profile:
    """Load and validate a profile; raise :class:`ProfileError` on any violation.

    The preview gate runs after structural validation: a manifest declaring
    ``write`` or ``approval_required`` tools fails startup naming the tool,
    and the tool allowlist must be a subset of the manifest's ``read`` tools.
    """

    profile_dir = profile_dir.resolve()
    if not profile_dir.is_dir():
        raise ProfileError(f"profile directory does not exist: {profile_dir}")

    manifest = _load_manifest(profile_dir)
    config = _load_config(profile_dir)
    identities = _load_identities(profile_dir)

    for tool in manifest.tools:
        if tool.permission in _PREVIEW_FORBIDDEN_PERMISSIONS:
            raise ProfileError(
                f"preview profile forbids tool {tool.name!r} with permission {tool.permission!r}; "
                "only 'read' tools are allowed (write/approval land in a later step)"
            )

    manifest_read_names = {tool.name for tool in manifest.tools if tool.permission == "read"}
    # The preview host provides a fixed builtin read tool; a manifest that
    # declares the same name must mark it read. Anything else in the
    # allowlist must be an explicitly declared read tool.
    builtin_read = {"query_inventory"}
    unknown = [name for name in config.tool_allowlist if name not in builtin_read and name not in manifest_read_names]
    if unknown:
        raise ProfileError(f"tool_allowlist entries not declared as read tools in the manifest: {unknown}")

    entry_path = profile_dir / "files" / manifest.entry
    entry_content = entry_path.read_bytes()

    shutdown_token = _resolve_secret_ref(config.shutdown_token_ref, profile_dir)

    return Profile(
        directory=profile_dir,
        manifest=manifest,
        config=config,
        identities=identities,
        shutdown_token=shutdown_token,
        entry_content=entry_content,
    )
