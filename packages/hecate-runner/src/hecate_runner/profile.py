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
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from hecate_runtime.manifest import ArtifactManifest, ManifestError, sha256_hex, validate_manifest_dict
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

PREVIEW_ROLE = "read_only"
# Without the durable profile only read tools may execute; the durable
# profile additionally admits manifest-declared ``write`` tools (the
# business API is the authorization/approval authority in standalone mode —
# SC03's "no platform approval service"). Approval-bound tools stay
# refused until step7 ships runner-side approval binding.
_PREVIEW_FORBIDDEN_PERMISSIONS = {"write", "approval_required"}
# step6c: durable waiting provides the approval-binding mechanics; the
# approval JUDGMENT stays with step7 — an approval_required tool parks
# the task into a persistent wait instead of dispatching.
_DURABLE_ALLOWED_PERMISSIONS = {"read", "write", "approval_required"}
BUILTIN_TOOL_SCHEMAS = {
    "query_inventory": {
        "type": "object",
        "required": ["domain", "sku"],
        "properties": {"domain": {"type": "string", "minLength": 1}, "sku": {"type": "string", "minLength": 1}},
    },
    # step6c builtin approval tool: dispatches never reach the business API;
    # the run parks into waiting_approval until a resume command applies.
    "submit_ticket": {
        "type": "object",
        "required": ["ticket"],
        "properties": {"ticket": {"type": "string", "minLength": 1}},
    },
    "submit_inventory_update": {
        "type": "object",
        "required": ["domain", "sku", "quantity"],
        "properties": {
            "domain": {"type": "string", "minLength": 1},
            "sku": {"type": "string", "minLength": 1},
            "quantity": {"type": "integer", "minimum": 1},
        },
    },
}


@dataclass(frozen=True)
class ControlPlaneConfig:
    """Managed-channel settings (optional overlay; step6/7 managed slice).

    All fields come from the optional ``control_plane`` block of
    ``runner.json``. Absent block = standalone mode, zero managed wiring.
    ``secret_ref`` names the trust material for the HMAC challenge and
    channel credentials; ``lease_ttl_seconds`` IS the plan's maximum stale
    window — after expiry without a successful pull, new protected actions
    stop (never falling back to local self-authorization).
    """

    base_url: str
    workspace_id: str
    trust_root: str
    host_id: str
    secret_ref: str
    issuer_domain: str
    lease_ttl_seconds: float = 120.0
    poll_interval_seconds: float = 5.0
    upload_batch: int = 100
    # Data domains managed deliveries may dispatch tools with. Empty (the
    # default) = deny-by-default: the run's existing domain check refuses
    # every domain-carrying dispatch until the operator maps a scope.
    data_domains: tuple[str, ...] = ()


def _load_control_plane(data: dict, profile_dir: Path) -> ControlPlaneConfig | None:
    """Parse and validate the optional ``control_plane`` block."""

    raw = data.get("control_plane")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ProfileError("invalid runner config: control_plane must be an object")
    base_url = raw.get("base_url")
    if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
        raise ProfileError("control_plane.base_url must be an http(s) URL")
    workspace_id = raw.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise ProfileError("control_plane.workspace_id must be a non-empty string")
    try:
        uuid.UUID(workspace_id)
    except ValueError as exc:
        raise ProfileError("control_plane.workspace_id must be a uuid") from exc
    trust_root = raw.get("trust_root")
    host_id = raw.get("host_id")
    if not isinstance(trust_root, str) or not trust_root or not isinstance(host_id, str) or not host_id:
        raise ProfileError("control_plane.trust_root and control_plane.host_id must be non-empty strings")
    issuer_domain = raw.get("issuer_domain")
    if not isinstance(issuer_domain, str) or not issuer_domain:
        raise ProfileError("control_plane.issuer_domain must be a non-empty string")
    secret_ref = raw.get("secret_ref")
    if not isinstance(secret_ref, str) or not secret_ref:
        raise ProfileError("control_plane.secret_ref must be a secret reference (env:NAME or file:path)")
    resolve_secret_ref(secret_ref, profile_dir)  # fail fast at startup
    lease_ttl = raw.get("lease_ttl_seconds", 120.0)
    poll = raw.get("poll_interval_seconds", 5.0)
    upload_batch = raw.get("upload_batch", 100)
    data_domains_raw = raw.get("data_domains", [])
    if not isinstance(lease_ttl, (int, float)) or lease_ttl <= 0:
        raise ProfileError("control_plane.lease_ttl_seconds must be a positive number")
    if not isinstance(poll, (int, float)) or poll <= 0:
        raise ProfileError("control_plane.poll_interval_seconds must be a positive number")
    if not isinstance(upload_batch, int) or upload_batch <= 0:
        raise ProfileError("control_plane.upload_batch must be a positive integer")
    if not isinstance(data_domains_raw, list) or any(not isinstance(d, str) or not d for d in data_domains_raw):
        raise ProfileError("control_plane.data_domains must be an array of non-empty strings")
    if len(data_domains_raw) != len(set(data_domains_raw)):
        raise ProfileError("control_plane.data_domains must not contain duplicate domains")
    data_domains = tuple(data_domains_raw)
    return ControlPlaneConfig(
        base_url=base_url.rstrip("/"),
        workspace_id=workspace_id,
        trust_root=trust_root,
        host_id=host_id,
        secret_ref=secret_ref,
        issuer_domain=issuer_domain,
        lease_ttl_seconds=float(lease_ttl),
        poll_interval_seconds=float(poll),
        upload_batch=upload_batch,
        data_domains=data_domains,
    )


@dataclass(frozen=True)
class DurableConfig:
    """Durable profile settings (persistent task/action ledger storage)."""

    database_url: str
    workspace: str


def _load_durable(data: dict) -> DurableConfig | None:
    """Parse and validate the optional ``durable`` block of ``runner.json``."""

    raw = data.get("durable")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ProfileError("invalid runner config: durable must be an object")
    url = raw.get("database_url")
    if not isinstance(url, str) or not url:
        raise ProfileError("invalid runner config: durable.database_url must be a non-empty string")
    if not (url.startswith("sqlite:///") or url.startswith("postgresql+psycopg://")):
        raise ProfileError("durable.database_url must be sqlite:/// (development) or postgresql+psycopg:// (reference)")
    workspace = raw.get("workspace", "standalone")
    if not isinstance(workspace, str) or not workspace:
        raise ProfileError("durable.workspace must be a non-empty string")
    return DurableConfig(database_url=url, workspace=workspace)


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
    durable: DurableConfig | None = None
    control_plane: ControlPlaneConfig | None = None
    evidence: EvidenceConfig | None = None


@dataclass(frozen=True)
class TrustedIdentity:
    """Server-side identity resolved from trust material."""

    principal: str
    role: str
    domains: tuple[str, ...]
    credential_sha256: str


def resolve_secret_ref(ref: str, profile_dir: Path) -> str:
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
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise ProfileError(f"secret reference file:{ref[5:]} is empty")
        return value
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


def _check_local_schema_refs(value: object) -> None:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if ref is not None and (not isinstance(ref, str) or not ref.startswith("#")):
            raise ProfileError("preview tool schemas support internal references only")
        for item in value.values():
            _check_local_schema_refs(item)
    elif isinstance(value, list):
        for item in value:
            _check_local_schema_refs(item)


def _load_manifest(profile_dir: Path) -> ArtifactManifest:
    raw = _load_json(profile_dir / "agent-manifest.json", "agent manifest")
    try:
        manifest = validate_manifest_dict(raw)
    except ManifestError as exc:
        raise ProfileError(f"invalid agent manifest: {exc}") from exc

    for entry in manifest.files:
        content_path = profile_dir / "files" / entry.path
        if not content_path.resolve().is_relative_to((profile_dir / "files").resolve()):
            raise ProfileError(f"manifest file escapes files directory: {entry.path!r}")
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
    if host not in ("127.0.0.1", "localhost"):
        errors.append("host must be a loopback address in the preview profile")
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
    if not isinstance(model_backend, str) or model_backend not in {"stub", "endpoint"}:
        errors.append("model.backend must be 'stub' or 'endpoint'")
    model_endpoint = model.get("endpoint")
    model_auth_env = model.get("auth_env")
    if model_backend == "endpoint" and (not isinstance(model_endpoint, str) or not model_endpoint):
        errors.append("model.endpoint is required when backend is 'endpoint'")
    elif model_backend == "endpoint":
        try:
            endpoint_url = urlsplit(model_endpoint if isinstance(model_endpoint, str) else "")
            if endpoint_url.scheme not in ("http", "https") or not endpoint_url.hostname:
                errors.append("model.endpoint must be an HTTP(S) URL")
        except ValueError:
            errors.append("model.endpoint must be an HTTP(S) URL")

    allowlist = data.get("tool_allowlist", [])
    if not isinstance(allowlist, list) or any(not isinstance(name, str) or not name for name in allowlist):
        errors.append("tool_allowlist must be an array of non-empty strings")
    elif len(allowlist) != len(set(allowlist)):
        errors.append("tool_allowlist must not contain duplicate tools")

    shutdown_ref = data.get("shutdown_token_ref")
    if not isinstance(shutdown_ref, str) or not shutdown_ref:
        errors.append("shutdown_token_ref must be a non-empty secret reference")

    max_request_bytes = data.get("max_request_bytes", 1 << 20)
    if not isinstance(max_request_bytes, int) or isinstance(max_request_bytes, bool) or max_request_bytes <= 0:
        errors.append("max_request_bytes must be a positive integer")

    max_concurrency = data.get("max_concurrency", 1)
    if not isinstance(max_concurrency, int) or isinstance(max_concurrency, bool) or max_concurrency != 1:
        errors.append("max_concurrency must be 1 in the preview profile")

    if errors:
        raise ProfileError("invalid runner config: " + "; ".join(errors))

    # Validation above guarantees a non-empty string; narrow without assert
    # (ruff S101) for the type checker.
    evidence_dir_str = evidence_dir if isinstance(evidence_dir, str) else ""

    durable_config = _load_durable(data)
    evidence_config = _load_evidence(data, profile_dir)
    if evidence_config is not None and evidence_config.backend == "durable" and durable_config is None:
        raise ProfileError("evidence.backend 'durable' requires the durable profile (durable.database_url)")

    return RunnerConfig(
        durable=durable_config,
        control_plane=_load_control_plane(data, profile_dir),
        evidence=evidence_config,
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


@dataclass(frozen=True)
class EvidenceConfig:
    """Evidence retention settings (optional top-level ``evidence`` block).

    ``retention_days``/``capacity_limit`` are ``None`` when not configured —
    the no-policy default is explicit (no limits, no expiry) and reported
    as such by the capability summary. Units are declared by the backend
    (jsonl: bytes; durable: rows).
    """

    dir_override: Path | None
    backend: str  # "jsonl" | "durable"
    retention_days: int | None
    capacity_limit: int | None


def _load_evidence(data: dict, profile_dir: Path) -> EvidenceConfig | None:
    """Parse and validate the optional ``evidence`` block of ``runner.json``."""

    raw = data.get("evidence")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ProfileError("invalid runner config: evidence must be an object")
    backend = raw.get("backend", "jsonl")
    if backend not in ("jsonl", "durable"):
        raise ProfileError("evidence.backend must be 'jsonl' or 'durable'")
    dir_raw = raw.get("dir")
    if dir_raw is not None and (not isinstance(dir_raw, str) or not dir_raw):
        raise ProfileError("evidence.dir must be a non-empty string")
    retention_days = raw.get("retention_days")
    if retention_days is not None and (
        not isinstance(retention_days, int) or isinstance(retention_days, bool) or retention_days < 1
    ):
        raise ProfileError("evidence.retention_days must be a positive integer")
    capacity_limit = raw.get("capacity_limit")
    if capacity_limit is not None and (
        not isinstance(capacity_limit, int) or isinstance(capacity_limit, bool) or capacity_limit < 1
    ):
        raise ProfileError("evidence.capacity_limit must be a positive integer")
    return EvidenceConfig(
        dir_override=(profile_dir / dir_raw) if isinstance(dir_raw, str) else None,
        backend=backend,
        retention_days=retention_days,
        capacity_limit=capacity_limit,
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

        material = resolve_secret_ref(credential, profile_dir)
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
    tool_schemas: dict[str, dict]

    def execution_definition_digest(self, dispatch_binding: str | None = None) -> str:
        """Fingerprint the fixed graph inputs and its business dispatch binding."""
        from hecate_durable.contracts.durable import canonical_request_digest

        return canonical_request_digest(
            {
                "graph_contract": "runner-linear-v1",
                "manifest": self.manifest.to_dict(),
                "tool_allowlist": list(self.config.tool_allowlist),
                "tool_schemas": self.tool_schemas,
                "model_backend": self.config.model_backend,
                "model_endpoint": self.config.model_endpoint,
                "model_auth_env": self.config.model_auth_env,
                "dispatch_binding": dispatch_binding,
            }
        )

    def capabilities_summary(self) -> dict:
        summary = {
            "backend_type": self.manifest.backend_type,
            "backend_compat_version": self.manifest.backend_compat_version,
            "model_source": self.config.model_backend,
            "read_tools": sorted(self.config.tool_allowlist),
        }
        if self.config.durable is not None:
            summary["durable"] = True
            summary["durable_workspace"] = self.config.durable.workspace
        evidence = self.config.evidence
        summary["evidence_backend"] = evidence.backend if evidence is not None else "jsonl"
        summary["evidence_retention_days"] = evidence.retention_days if evidence is not None else None
        summary["evidence_capacity_limit"] = evidence.capacity_limit if evidence is not None else None
        # The no-policy default is declared, not silent: no limits, no expiry.
        summary["evidence_policy_configured"] = evidence is not None
        return summary


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
    if manifest.backend_type != "pregel" or manifest.backend_compat_version != "0.1":
        raise ProfileError("preview supports only backend_type='pregel' with backend_compat_version='0.1'")
    if manifest.contract_version != "0.1":
        raise ProfileError("preview supports only contract_version='0.1'")
    supported = {"submit", "read_events_cursor", "cancel", "local_evidence"}
    if set(manifest.required_capabilities) - supported:
        raise ProfileError("manifest requires unsupported capabilities")
    config = _load_config(profile_dir)
    identities = _load_identities(profile_dir)

    allowed_permissions = _DURABLE_ALLOWED_PERMISSIONS if config.durable is not None else {"read"}
    for tool in manifest.tools:
        if tool.permission not in allowed_permissions:
            qualifier = (
                "only 'read' tools are allowed"
                if config.durable is None
                else (
                    "only 'read', durable-profile 'write', and 'approval_required' (persistent wait) tools are allowed"
                )
            )
            raise ProfileError(f"profile forbids tool {tool.name!r} with permission {tool.permission!r}; {qualifier}")

    manifest_admitted = {tool.name for tool in manifest.tools if tool.permission in allowed_permissions}
    # The preview host provides fixed builtin tools; a manifest that declares
    # the same name must carry an admitted permission. Anything else in the
    # allowlist must be an explicitly declared tool.
    builtin_admitted = set(BUILTIN_TOOL_SCHEMAS)
    unknown = [name for name in config.tool_allowlist if name not in builtin_admitted and name not in manifest_admitted]
    if unknown:
        raise ProfileError(
            f"tool_allowlist entries not declared with an admitted permission in the manifest: {unknown}"
        )
    unmapped = set(config.tool_allowlist) - set(BUILTIN_TOOL_SCHEMAS)
    if unmapped:
        raise ProfileError(f"preview has no tool adapter for: {sorted(unmapped)}")

    schemas = {name: BUILTIN_TOOL_SCHEMAS[name] for name in config.tool_allowlist}
    verified_files = {entry.path for entry in manifest.files}
    for tool in manifest.tools:
        if tool.name not in config.tool_allowlist:
            continue
        if tool.schema_ref not in verified_files:
            raise ProfileError(f"tool schema must be a digest-verified manifest file: {tool.schema_ref!r}")
        schema = _load_json(profile_dir / "files" / tool.schema_ref, f"tool schema {tool.name}")
        _check_local_schema_refs(schema)
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ProfileError(f"invalid tool schema for {tool.name}") from exc
        schemas[tool.name] = schema

    entry_path = profile_dir / "files" / manifest.entry
    entry_content = entry_path.read_bytes()
    entry = _load_json(entry_path, "preview entry")
    if entry.get("kind") != "entry" or entry.get("tools") != list(config.tool_allowlist):
        raise ProfileError("preview entry must declare kind='entry' and the configured tools in order")

    shutdown_token = resolve_secret_ref(config.shutdown_token_ref, profile_dir)
    if config.model_auth_env and not os.environ.get(config.model_auth_env):
        raise ProfileError(f"model auth reference env:{config.model_auth_env} is not set")

    return Profile(
        directory=profile_dir,
        manifest=manifest,
        config=config,
        identities=identities,
        shutdown_token=shutdown_token,
        entry_content=entry_content,
        tool_schemas=schemas,
    )
