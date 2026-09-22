"""Dual-format plugin convergence — Hecate's Agent Plugins namespace.

One Agent Plugins 1.0 package is simultaneously a conformant open-standard
plugin (plugin.json + skills/ + mcp.json, visible to every client) and a
Hecate deep-integration plugin. The private face lives under the namespace
directory named :data:`AGENT_PLUGIN_NAMESPACE` (spec section 8.2 extension
directories): a ``plugin.yaml`` manifest plus the code payload referenced
by its ``entry``. Clients that do not implement the namespace ignore the
directory entirely.

``AGENT_PLUGIN_NAMESPACE`` is a format identity, not deployment policy: a
package carries the same namespace everywhere. The guard test pins the
value so changing it is a deliberate, migration-worthy decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hecate.core.plugin.agent_plugins import AgentPluginValidationError
from hecate.core.plugin.manifest import PluginManifest

AGENT_PLUGIN_NAMESPACE = "io.github.xueyufish"
NAMESPACE_DIR_NAME = AGENT_PLUGIN_NAMESPACE
NAMESPACE_MANIFEST_NAME = "plugin.yaml"

AGENT_PLUGIN_SCHEMA_URL = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"

# Fields permitted in the namespace manifest (the legacy Hecate manifest
# model). ``name``/``version`` are identity fields owned by plugin.json —
# allowed but must match, or the package is rejected (replace-not-merge).
# ``requires`` (5.9e) is the plugin-level skill dependency list; each
# element is a ``{name, provider?}`` entry applied to every skill
# imported from the package.
NAMESPACE_MANIFEST_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "version",
        "type",
        "api_version",
        "min_platform_version",
        "description",
        "entry",
        "permissions",
        "translations",
        "config_schema",
        "metadata",
        "requires",
    }
)

# Spec name grammar is enforced loosely here (the canonical validator is
# ``agent_plugins._NAME_RE``); sanitization produces grammar-conforming
# output for bundle names and skill directory names.
_SANITIZE_DISALLOWED = re.compile(r"[^a-z0-9-]")
_SANITIZE_DUPES = re.compile(r"-{2,}|\.{2,}")


class NamespaceManifestError(AgentPluginValidationError):
    """Raised when a namespace manifest exists but is malformed.

    Callers degrade the package to a plain agent-plugin with a warning
    (skip-and-continue); identity conflicts raise the parent class instead
    and reject the package outright.
    """


@dataclass(frozen=True)
class ResolvedNamespace:
    """The Hecate-private face of a dual-format package."""

    manifest: PluginManifest
    ns_dir: Path
    metadata: dict[str, Any]
    # 5.9e: plugin-level skill dependency list. Empty list when the
    # package declares no requires. Validated against the workspace at
    # install time by the agent_plugins pipeline (fail-closed).
    requires: tuple[dict[str, Any], ...] = ()


def namespace_manifest_path(package_root: Path) -> Path:
    """Path of the namespace manifest inside a package root."""
    return package_root / NAMESPACE_DIR_NAME / NAMESPACE_MANIFEST_NAME


def check_namespace_extension_value(manifest: dict[str, Any], warnings: list[str]) -> None:
    """Warn when the ``extensions`` value for the Hecate namespace is not an object.

    The namespace directory is the content source; the extensions value is
    pointer metadata only and never overrides it. Values for other
    namespaces are ignored entirely (spec: unimplemented extensions are
    ignored without validating values).
    """
    extensions = manifest.get("extensions")
    if not isinstance(extensions, dict):
        return
    value = extensions.get(AGENT_PLUGIN_NAMESPACE)
    if value is not None and not isinstance(value, dict):
        warnings.append(f"extensions[{AGENT_PLUGIN_NAMESPACE!r}] is not an object; ignoring value {value!r}")


def resolve_namespace(package_root: Path, plugin_json: dict[str, Any]) -> ResolvedNamespace | None:
    """Resolve the Hecate namespace face of a package, if any.

    Returns ``None`` when the package carries no namespace manifest (plain
    agent-plugin). Raises :class:`NamespaceManifestError` when a namespace
    manifest exists but is malformed (caller degrades with a warning), and
    :class:`AgentPluginValidationError` when its name/version conflicts
    with plugin.json (caller rejects the package).
    """
    manifest_path = namespace_manifest_path(package_root)
    if not manifest_path.is_file():
        return None
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise NamespaceManifestError(f"namespace plugin.yaml is not valid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise NamespaceManifestError("namespace plugin.yaml must be a mapping")

    unknown = set(raw) - NAMESPACE_MANIFEST_FIELDS
    if unknown:
        raise NamespaceManifestError(f"namespace manifest has fields outside the closed model: {sorted(unknown)}")

    for field in ("name", "version"):
        declared = raw.get(field)
        open_value = plugin_json.get(field)
        if declared is not None and open_value is not None and str(declared) != str(open_value):
            msg = f"namespace manifest {field}={declared!r} conflicts with plugin.json {field}={open_value!r}"
            raise AgentPluginValidationError(msg)

    entry = raw.get("entry")
    if entry is not None and not isinstance(entry, str):
        raise NamespaceManifestError("entry must be a string")
    plugin_type = raw.get("type")
    if entry and not plugin_type:
        raise NamespaceManifestError("namespace manifest with an entry must declare a type")
    permissions = raw.get("permissions")
    if permissions is not None and (
        not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions)
    ):
        raise NamespaceManifestError("permissions must be a list of strings")
    config_schema = raw.get("config_schema")
    if config_schema is not None and not isinstance(config_schema, dict):
        raise NamespaceManifestError("config_schema must be a mapping")
    metadata = raw.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise NamespaceManifestError("metadata must be a mapping")

    # 5.9e: optional plugin-level skill dependency list. The shape mirrors
    # SKILL.md frontmatter ``requires`` — a list of ``{name, provider?}``
    # entries. Structural shape is checked here; graph validation
    # (missing / cycle / cross-source) runs at install time in the
    # ``agent_plugins`` ingestion pipeline so the error message points at
    # the package, not at the parser.
    raw_requires = raw.get("requires")
    if raw_requires is None:
        requires_tuple: tuple[dict[str, Any], ...] = ()
    elif isinstance(raw_requires, list):
        requires_tuple = tuple(e for e in raw_requires if isinstance(e, dict))
    else:
        raise NamespaceManifestError("requires must be a list of {name, provider?} entries")

    manifest = PluginManifest(
        type=str(plugin_type or ""),
        name=str(raw.get("name") or plugin_json.get("name") or ""),
        version=str(raw.get("version") or plugin_json.get("version") or "0.0.0"),
        api_version=str(raw.get("api_version") or ""),
        min_platform_version=str(raw.get("min_platform_version") or ""),
        description=str(raw.get("description") or ""),
        entry=entry or "",
        permissions=tuple(permissions or ()),
        translations=tuple(raw.get("translations") or ()),
        config_schema=config_schema,
    )
    return ResolvedNamespace(
        manifest=manifest,
        ns_dir=package_root / NAMESPACE_DIR_NAME,
        metadata=dict(metadata or {}),
        requires=requires_tuple,
    )


def build_plugin_json(
    manifest: dict[str, Any],
    *,
    author: str | None = None,
    homepage: str | None = None,
    repository: str | None = None,
) -> dict[str, Any]:
    """Build an open-face plugin.json from a Hecate plugin.yaml mapping.

    Identity (name/version) moves into plugin.json; the namespace manifest
    written alongside it omits both fields.
    """
    if not manifest.get("name"):
        msg = "plugin.yaml must declare a name to generate plugin.json"
        raise ValueError(msg)
    plugin_json: dict[str, Any] = {
        "$schema": AGENT_PLUGIN_SCHEMA_URL,
        "name": manifest["name"],
        "version": str(manifest.get("version") or "0.1.0"),
    }
    if manifest.get("description"):
        plugin_json["description"] = manifest["description"]
    if author:
        plugin_json["author"] = {"name": author}
    if homepage:
        plugin_json["homepage"] = homepage
    if repository:
        plugin_json["repository"] = repository
    return plugin_json


def sanitize_package_name(name: str, *, max_length: int = 64) -> str:
    """Sanitize a name into the Agent Plugins name grammar.

    Lowercase, ``[a-z0-9-]`` only, no ``--``/``..`` runs, alphanumeric
    start/end. Deterministic; empty input falls back to ``"plugin"``.
    """
    cleaned = _SANITIZE_DUPES.sub("-", name.lower().strip())
    cleaned = _SANITIZE_DISALLOWED.sub("-", cleaned)
    cleaned = _SANITIZE_DUPES.sub("-", cleaned).strip("-._")
    cleaned = cleaned[:max_length].rstrip("-._")
    if not cleaned or not cleaned[0].isalnum():
        cleaned = f"p{cleaned}".rstrip("-._") if cleaned else "plugin"
    return cleaned
