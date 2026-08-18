"""Agent Plugins 1.0 ingestion adapter (feature 5.5c).

Single-module adapter for the Agent Plugins 1.0 open standard
(https://agent-plugins.org, spec 1.0.0 published 2026-08-06). A package is
a directory with a closed ``plugin.json`` manifest, optional ``skills/``
component (one SKILL.md per immediate child directory), and an optional
root-level ``mcp.json`` declaring MCP server dependencies.

The adapter deliberately touches nothing in the existing PluginManifest /
8-type-ABC stack: format volatility is hedged behind this one module.
Distribution, trust, and signing are intentionally out of the spec's scope;
this module implements Hecate's client-side half — closed-manifest
validation (offline), fixed-location discovery with component-level
skip-and-continue, path containment, and translation of package components
into platform projections (SkillModel rows, MCP registrations).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# --- Closed content model constants (spec 1.0.0) --------------------------

ALLOWED_MANIFEST_FIELDS: frozenset[str] = frozenset(
    {
        "$schema",
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "extensions",
    }
)
ALLOWED_AUTHOR_FIELDS: frozenset[str] = frozenset({"name", "email", "url"})
SUPPORTED_SCHEMA_VERSIONS: frozenset[str] = frozenset({"1.0.0"})

# Spec name grammar: 1-64 chars, lowercase a-z 0-9 . -, alphanumeric
# start/end, no "--" and no "..".
_NAME_RE = re.compile(r"^[a-z0-9](?!.*--)(?!.*\.\.)[a-z0-9.-]{0,62}[a-z0-9]$|^[a-z0-9]$")

# Credential-looking substrings forbidden in mcp.json header values (the
# spec forbids embedded secrets in headers).
_HEADER_SECRET_MARKERS: tuple[str, ...] = (
    "bearer ",
    "sk-",
    "apikey",
    "api_key",
    "api-key",
    "secret",
    "password",
    "token=",
)

# Patterns in stdio args/env that expand to arbitrary code execution.
_STDIO_DANGEROUS_ARG_MARKERS: tuple[str, ...] = (
    "&&",
    "||",
    "|",
    ";",
    "$(",
    "`",
    ">",
    "<",
)

# Safety cap on extracted zip entries (zip-bomb mitigation).
MAX_ZIP_ENTRIES = 5000

GIT_TIMEOUT_SECONDS = 60


class AgentPluginValidationError(ValueError):
    """Raised when a package violates the Agent Plugins 1.0 contract."""


# --- Manifest validation (task 2.1) ----------------------------------------


@dataclass
class ManifestValidationResult:
    """Outcome of closed-manifest validation for plugin.json."""

    manifest: dict[str, Any]
    schema_version: str
    warnings: list[str] = field(default_factory=list)


def _schema_version_of(value: Any) -> str | None:
    """Extract a known schema version from a $schema identifier, if any.

    The spec says clients use ``$schema`` to pick local validation rules
    and must never fetch it. We accept the canonical identifier forms seen
    in the wild (bare ``1.0.0`` or URL ending in the version).
    """
    if not isinstance(value, str):
        return None
    for version in SUPPORTED_SCHEMA_VERSIONS:
        if value == version or f"/{version}/" in value or value.rstrip("/").endswith(f"/{version}"):
            return version
    return None


def validate_plugin_json(data: Any) -> ManifestValidationResult:
    """Validate a parsed plugin.json against the closed 1.0.0 model.

    Unknown top-level fields warn and are ignored; every other violation
    (missing/invalid required fields, unrecognized ``$schema``, malformed
    ``author``) rejects the package. Fully offline — no schema fetching.
    """
    warnings: list[str] = []

    if not isinstance(data, dict):
        raise AgentPluginValidationError("plugin.json must be a JSON object")

    schema_version = _schema_version_of(data.get("$schema"))
    if schema_version is None:
        raise AgentPluginValidationError(
            f"Unrecognized $schema: {data.get('$schema')!r} (supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
        )

    name = data.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise AgentPluginValidationError(
            f"Invalid plugin name {name!r}: must be 1-64 chars of lowercase "
            "a-z/0-9/./- with alphanumeric start/end and no '--'/'..'"
        )

    author = data.get("author")
    if author is not None:
        if not isinstance(author, dict):
            raise AgentPluginValidationError("author must be an object")
        extra = set(author) - ALLOWED_AUTHOR_FIELDS
        if extra:
            raise AgentPluginValidationError(f"author has fields outside the closed model: {sorted(extra)}")

    for string_field in ("version", "description", "homepage", "repository", "license"):
        value = data.get(string_field)
        if value is not None and not isinstance(value, str):
            raise AgentPluginValidationError(f"{string_field} must be a string")

    keywords = data.get("keywords")
    if keywords is not None and (not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords)):
        raise AgentPluginValidationError("keywords must be a list of strings")

    extensions = data.get("extensions")
    if extensions is not None and not isinstance(extensions, dict):
        # Spec: a non-object ``extensions`` is non-fatal — warn and ignore.
        warnings.append(f"extensions is not an object; ignoring value {extensions!r}")

    unknown = set(data) - ALLOWED_MANIFEST_FIELDS
    for key in sorted(unknown):
        warnings.append(f"Unknown top-level field {key!r} ignored")

    return ManifestValidationResult(manifest=data, schema_version=schema_version, warnings=warnings)


def read_manifest(package_root: Path) -> dict[str, Any]:
    """Read and parse plugin.json from a package root."""
    manifest_path = package_root / "plugin.json"
    if not manifest_path.is_file():
        raise AgentPluginValidationError(f"No plugin.json at package root {package_root}")
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise AgentPluginValidationError(f"Cannot read plugin.json: {e}") from e


# --- Path containment (task 2.3) -------------------------------------------


def resolve_contained(root: Path, relative: Path | str) -> Path:
    """Resolve *relative* inside *root*, rejecting symlink escapes.

    Every filesystem path the ingester touches must pass through here so
    that the resolved real path stays inside the package root.
    """
    root_real = root.resolve()
    candidate = (root / relative).resolve()
    if candidate != root_real and root_real not in candidate.parents:
        raise AgentPluginValidationError(f"Path escapes package root: {relative} -> {candidate}")
    return candidate


# --- Digests and size caps (task 2.4) ---------------------------------------


def compute_tree_digest(root: Path) -> str:
    """Content digest of a materialized package tree, excluding .git."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if ".git" not in p.parts):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"


def tree_size_bytes(root: Path) -> int:
    """Total size in bytes of all regular files under *root*."""
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def check_size_caps(
    package_root: Path,
    max_package_bytes: int,
    workspace_usage_bytes: int = 0,
    max_workspace_bytes: int | None = None,
) -> None:
    """Enforce per-package and per-workspace size caps on install."""
    size = tree_size_bytes(package_root)
    if size > max_package_bytes:
        raise AgentPluginValidationError(f"Package size {size} bytes exceeds per-package cap {max_package_bytes} bytes")
    if max_workspace_bytes is not None and workspace_usage_bytes + size > max_workspace_bytes:
        raise AgentPluginValidationError(
            f"Workspace aggregate {workspace_usage_bytes + size} bytes would "
            f"exceed per-workspace cap {max_workspace_bytes} bytes"
        )


# --- Source descriptors / origin provenance (task 2.2, D13) ----------------


@dataclass
class SourceDescriptor:
    """Install-source provenance for pin-by-hash (design D13)."""

    type: str  # "dir" | "git" | "zip"
    location: str
    ref: str | None = None
    commit_sha: str | None = None
    content_digest: str | None = None

    def to_origin(self) -> str:
        parts = [self.type, self.location]
        if self.ref:
            parts.append(f"ref={self.ref}")
        if self.commit_sha:
            parts.append(f"sha={self.commit_sha}")
        if self.content_digest:
            parts.append(f"digest={self.content_digest}")
        return ":".join(parts)


# --- Materialization (task 2.2) ---------------------------------------------


def materialize_from_dir(source: Path, dest: Path) -> SourceDescriptor:
    """Materialize a local directory source into an immutable snapshot."""
    if not source.is_dir():
        raise AgentPluginValidationError(f"Source directory not found: {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest, symlinks=True, dirs_exist_ok=False)
    return SourceDescriptor(type="dir", location=str(source))


def materialize_from_zip(zip_path: Path, dest: Path) -> SourceDescriptor:
    """Safely extract a ZIP (transport-only) into a snapshot directory."""
    import zipfile

    if not zip_path.is_file():
        raise AgentPluginValidationError(f"Zip file not found: {zip_path}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            entries = zf.namelist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise AgentPluginValidationError(f"Zip has {len(entries)} entries; cap is {MAX_ZIP_ENTRIES}")
            for entry in entries:
                entry_path = Path(entry)
                if entry_path.is_absolute() or ".." in entry_path.parts:
                    raise AgentPluginValidationError(f"Unsafe zip entry: {entry}")
            zf.extractall(dest)
    except zipfile.BadZipFile as e:
        raise AgentPluginValidationError(f"Corrupted zip: {e}") from e
    return SourceDescriptor(type="zip", location=str(zip_path))


def materialize_from_git(url: str, dest: Path, ref: str | None = None) -> SourceDescriptor:
    """Clone a public git repository (no credentials in v1) and record the
    ref + commit SHA + content digest provenance triple."""
    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [url, str(dest)]
    try:
        subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise AgentPluginValidationError(f"git clone failed for {url}: {e}") from e

    sha_proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S603, S607
        cwd=dest,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )
    commit_sha = sha_proc.stdout.strip() if sha_proc.returncode == 0 else None

    # Support repos that are themselves a plugin package (plugin.json at
    # root) as well as monorepos exposing one under a single top-level dir.
    package_root = _locate_package_root(dest)

    return SourceDescriptor(
        type="git",
        location=url,
        ref=ref,
        commit_sha=commit_sha,
        content_digest=compute_tree_digest(package_root),
    )


def _locate_package_root(clone_root: Path) -> Path:
    """Find the package root inside a git clone (root or single child)."""
    if (clone_root / "plugin.json").is_file() or _is_bare_skill_dir(clone_root):
        return clone_root
    children = [p for p in clone_root.iterdir() if p.is_dir() and p.name != ".git"]
    if len(children) == 1 and ((children[0] / "plugin.json").is_file() or _is_bare_skill_dir(children[0])):
        return children[0]
    raise AgentPluginValidationError(f"No Agent Plugins package found in git clone {clone_root}")


def relocate_snapshot(package_root: Path, final_dir: Path) -> None:
    """Move a validated snapshot tree from its staging place to its home."""
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    if package_root != final_dir:
        shutil.move(str(package_root), str(final_dir))


# --- Bare SKILL.md directory detection (task 2.5) --------------------------


def _is_bare_skill_dir(root: Path) -> bool:
    """True when *root* has no plugin.json but children carry SKILL.md."""
    if (root / "plugin.json").is_file():
        return False
    skills_root = root / "skills"
    if skills_root.is_dir():
        return any(child.is_dir() and (child / "SKILL.md").is_file() for child in skills_root.iterdir())
    return any(child.is_dir() and (child / "SKILL.md").is_file() for child in root.iterdir())


def detect_package_kind(package_root: Path) -> str:
    """Classify a snapshot: ``"agent-plugin"`` or ``"virtual"`` (bare skills)."""
    if (package_root / "plugin.json").is_file():
        return "agent-plugin"
    if _is_bare_skill_dir(package_root):
        return "virtual"
    raise AgentPluginValidationError(f"Neither plugin.json nor bare SKILL.md layout found in {package_root}")


# --- Skills discovery and import mapping (tasks 3.1/3.2) --------------------


@dataclass
class DiscoveredSkill:
    """A skill candidate found under skills/ (immediate children only)."""

    dir_name: str
    skill_md: Path


def discover_skills(package_root: Path) -> list[DiscoveredSkill]:
    """Discover skills in immediate child directories of ``skills/``.

    Per spec there is no recursion: nested SKILL.md files are supporting
    data. Non-conforming children are simply not discovered.

    Standard packages scan ``skills/``; bare (virtual) packages without a
    plugin.json scan their root-level children (Claude Code layout).
    """
    skills_root = package_root / "skills"
    if skills_root.is_dir():
        parent_dir, rel_base = skills_root, Path("skills")
    elif not (package_root / "plugin.json").is_file():
        parent_dir, rel_base = package_root, Path(".")
    else:
        return []
    found: list[DiscoveredSkill] = []
    for child in sorted(parent_dir.iterdir()):
        if not child.is_dir():
            continue
        try:
            rel = child.name / Path("SKILL.md") if rel_base == Path(".") else rel_base / child.name / "SKILL.md"
            resolved = resolve_contained(package_root, rel)
        except AgentPluginValidationError:
            continue
        if resolved.is_file():
            found.append(DiscoveredSkill(dir_name=child.name, skill_md=resolved))
    return found


@dataclass
class SkillImportCandidate:
    """Parsed skill ready for SkillModel persistence."""

    name: str
    description: str
    instructions: str
    extra: dict[str, Any]


def parse_skill_candidate(discovered: DiscoveredSkill) -> SkillImportCandidate:
    """Parse a discovered SKILL.md into an import candidate.

    Hecate hardening: frontmatter ``name`` must equal the directory name;
    a mismatch skips the skill (raised as ValueError for the orchestrator
    to record and continue).
    """
    from hecate.services.skill.parser import parse_skill_md

    content = discovered.skill_md.read_text(encoding="utf-8")
    parsed = parse_skill_md(content)

    if parsed["name"] != discovered.dir_name:
        raise ValueError(f"Skill name {parsed['name']!r} does not match directory name {discovered.dir_name!r}")

    # Re-read frontmatter for optional fields the base parser doesn't map.
    frontmatter: dict[str, Any] = {}
    stripped = content.strip()
    if stripped.startswith("---"):
        end = stripped.find("---", 3)
        if end != -1:
            loaded = yaml.safe_load(stripped[3:end])
            if isinstance(loaded, dict):
                frontmatter = loaded

    extra: dict[str, Any] = {}
    for key in ("license", "compatibility", "metadata", "allowed-tools", "allowed_tools"):
        if key in frontmatter:
            extra[key] = frontmatter[key]
    allowed = frontmatter.get("allowed-tools") or frontmatter.get("allowed_tools")
    if allowed is not None:
        extra["allowed-tools"] = allowed

    return SkillImportCandidate(
        name=parsed["name"],
        description=parsed["description"],
        instructions=parsed["instructions"],
        extra=extra,
    )


# --- mcp.json parsing and translation (task 3.3) ----------------------------


@dataclass
class McpServerSpec:
    """A translated mcp.json entry ready for projection."""

    server_name: str
    transport: str  # "http" (streamable-http/sse) | "stdio"
    endpoint: str  # URL for http; command for stdio
    headers: dict[str, str] = field(default_factory=dict)
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None


@dataclass
class McpValidationOutcome:
    """Result of translating mcp.json into server specs."""

    servers: list[McpServerSpec] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    disabled_reason: str | None = None  # set when the whole MCP component is off


def _is_loopback(url: str) -> bool:
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")  # noqa: S104


def _header_has_credentials(headers: dict[str, Any]) -> bool:
    for value in headers.values():
        if not isinstance(value, str):
            continue
        lowered = value.lower()
        if any(marker in lowered for marker in _HEADER_SECRET_MARKERS):
            return True
    return False


def validate_mcp_json(data: Any, plugin_schema_version: str) -> McpValidationOutcome:
    """Validate mcp.json and translate entries into MCP server specs.

    A ``$schema`` version mismatch with plugin.json disables only the MCP
    component (skills still import). Per-entry violations skip that server.
    """
    outcome = McpValidationOutcome()

    if not isinstance(data, dict):
        outcome.disabled_reason = "mcp.json is not a JSON object"
        return outcome

    schema_value = data.get("$schema")
    if schema_value is not None:
        version = _schema_version_of(schema_value)
        if version != plugin_schema_version:
            outcome.disabled_reason = (
                f"mcp.json $schema {schema_value!r} does not match plugin.json "
                f"version {plugin_schema_version!r}; MCP component disabled"
            )
            return outcome

    unknown = set(data) - {"$schema", "mcpServers"}
    for key in sorted(unknown):
        outcome.warnings.append(f"Unknown mcp.json top-level field {key!r} ignored")

    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        outcome.disabled_reason = "mcpServers must be an object"
        return outcome

    stdio_fields = {"type", "command", "args", "env", "cwd"}
    http_fields = {"type", "url", "headers"}

    for name, entry in servers.items():
        if not isinstance(entry, dict):
            outcome.warnings.append(f"mcp server {name!r}: entry must be an object; skipped")
            continue
        entry_type = entry.get("type")
        try:
            if entry_type == "stdio":
                extra = set(entry) - stdio_fields
                if extra:
                    raise ValueError(f"cross-variant fields {sorted(extra)}")
                _translate_stdio(name, entry, outcome)
            elif entry_type in ("streamable-http", "sse"):
                extra = set(entry) - http_fields
                if extra:
                    raise ValueError(f"cross-variant fields {sorted(extra)}")
                _translate_http(name, entry, outcome)
            else:
                raise ValueError(f"unsupported type {entry_type!r}")
        except ValueError as e:
            outcome.warnings.append(f"mcp server {name!r}: {e}; skipped")
    return outcome


def _translate_stdio(name: str, entry: dict[str, Any], outcome: McpValidationOutcome) -> None:
    command = entry.get("command")
    if not isinstance(command, str) or not command:
        raise ValueError("stdio requires a command")
    # Single executable token: bare name or ./-prefixed path.
    if "/" in command and not command.startswith("./"):
        raise ValueError("command must be a bare name or ./-prefixed path")
    if command.startswith("./") and any(seg == ".." for seg in Path(command).parts):
        raise ValueError("command path must not traverse upwards")

    args = entry.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError("args must be a list of strings")
    env = entry.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise ValueError("env must be a string map")
    cwd = entry.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        raise ValueError("cwd must be a string")
    if cwd is not None and not (
        cwd.startswith("./") or cwd.startswith("${PLUGIN_ROOT}") or cwd.startswith("${PLUGIN_DATA}")
    ):
        raise ValueError("cwd must be ./, ${PLUGIN_ROOT}-, or ${PLUGIN_DATA}-rooted")

    outcome.servers.append(
        McpServerSpec(
            server_name=name,
            transport="stdio",
            endpoint=command,
            args=list(args),
            env=dict(env),
            cwd=cwd,
        )
    )


def _translate_http(name: str, entry: dict[str, Any], outcome: McpValidationOutcome) -> None:
    from urllib.parse import urlparse

    url = entry.get("url")
    if not isinstance(url, str) or urlparse(url).scheme not in ("http", "https"):
        raise ValueError("streamable-http/sse requires an absolute http(s) url")
    if urlparse(url).username or urlparse(url).fragment:
        raise ValueError("url must not contain userinfo or a fragment")
    if url.startswith("http://") and not _is_loopback(url):
        raise ValueError("HTTPS required for non-loopback URLs")

    headers = entry.get("headers", {})
    if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise ValueError("headers must be a string map")
    if _header_has_credentials(headers):
        raise ValueError("header values must not contain credentials")

    outcome.servers.append(
        McpServerSpec(
            server_name=name,
            transport="http",
            endpoint=url,
            headers={k.lower(): v for k, v in headers.items()},
        )
    )


def read_mcp_json(package_root: Path) -> Any:
    """Read mcp.json from the package root; None when absent."""
    mcp_path = package_root / "mcp.json"
    if not mcp_path.is_file():
        return None
    try:
        return json.loads(mcp_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise AgentPluginValidationError(f"Cannot read mcp.json: {e}") from e


# --- stdio trust gating helpers (task 6.1, defined here for cohesion) ------


def check_stdio_entry(spec: McpServerSpec, command_allowlist: list[str]) -> str | None:
    """Return a denial reason for a stdio entry, or None when allowed.

    Fail-closed: an empty/invalid allowlist denies everything; commands
    outside the allowlist deny; args/env that would expand to arbitrary
    code execution deny.
    """
    if not command_allowlist:
        return "stdio command allowlist is empty — fail-closed denial"
    if spec.endpoint not in command_allowlist:
        return f"command {spec.endpoint!r} not in allowlist {command_allowlist}"
    for arg in spec.args:
        # ``-c`` style interpreter flags are exact-token matches (a hyphenated
        # word like "from-container" must not false-positive); the rest are
        # substring markers for shell metacharacters.
        if arg in ("-c", "-e", "-sh") or any(marker in arg for marker in _STDIO_DANGEROUS_ARG_MARKERS):
            return f"arg {arg!r} may expand to arbitrary code execution"
    for key, value in spec.env.items():
        lowered = value.lower()
        if any(marker in lowered for marker in ("$(", "`", "&&", ";")):
            return f"env {key!r} value may expand to arbitrary code execution"
    return None


# --- Scan stage slot (task 4.2, protocol defined here) ----------------------


@dataclass
class ScanResult:
    """Findings from the install-time scan stage (5.13a provides the real
    implementation; this dataclass fixes the interface shape)."""

    verdict: str  # "allow" | "warn" | "block"
    findings: list[dict[str, Any]] = field(default_factory=list)
    scanner_version: str = "noop"


class ScanStage:
    """Pipeline slot between validation and persistence (design D11).

    v1 no-op: leaves ``scan_result`` null. 5.13a replaces the
    implementation behind the same protocol.
    """

    def scan(self, package_root: Path) -> ScanResult | None:  # noqa: ARG002
        """No-op scan — returns None so ``scan_result`` stays null."""
        return None


# --- Component inventory (task 5.4 shape) -----------------------------------


@dataclass
class ComponentInventory:
    """Per-component import outcomes persisted in PluginModel.manifest_."""

    skills: list[dict[str, Any]] = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)

    def add_skill(self, name: str, status: str, reason: str | None = None) -> None:
        entry: dict[str, Any] = {"name": name, "status": status}
        if reason:
            entry["reason"] = reason
        self.skills.append(entry)

    def add_mcp_server(self, name: str, status: str, reason: str | None = None) -> None:
        entry: dict[str, Any] = {"name": name, "status": status}
        if reason:
            entry["reason"] = reason
        self.mcp_servers.append(entry)


def staging_dir(base: Path, package_name: str) -> Path:
    """Collision-free staging directory for materialization."""
    return base / f".staging-{package_name}-{uuid.uuid4().hex[:8]}"


def with_temp_zip(source: Path | bytes) -> Any:
    """Write zip bytes to a temp file (helper for API uploads)."""
    if isinstance(source, bytes):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")  # noqa: SIM115
        tmp.write(source)
        tmp.close()
        return Path(tmp.name)
    return source
