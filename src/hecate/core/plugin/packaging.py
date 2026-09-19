"""Plugin bundle packaging — create and extract .hecate-plugin ZIP archives."""

from __future__ import annotations

import json
import logging
import shutil
import zipfile
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

BUNDLE_EXTENSION = ".hecate-plugin"

# Top-level entries with special placement when emitting a dual-format
# tree: manifest sources are replaced (plugin.json is generated, the
# manifest relocates into the namespace directory) and open-face
# components pass through to the output root; everything else moves into
# the namespace directory as private payload.
_DUAL_SKIP = frozenset({"plugin.yaml", "plugin.json", ".git", "__pycache__"})
_OPEN_FACE_PASSTHROUGH = frozenset({"skills", "mcp.json"})


def validate_bundle(bundle_path: Path) -> bool:
    """Check that *bundle_path* is a valid ZIP containing plugin.yaml."""
    if not bundle_path.is_file():
        return False
    try:
        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()
            return any(n.endswith("plugin.yaml") for n in names)
    except zipfile.BadZipFile:
        return False


def create_bundle(plugin_dir: Path, output_path: Path | None = None) -> Path:
    """Package *plugin_dir* into a ``.hecate-plugin`` ZIP archive.

    Validates that ``plugin.yaml`` exists and contains required fields.

    Returns the path to the created bundle.
    """
    manifest_path = plugin_dir / "plugin.yaml"
    if not manifest_path.is_file():
        msg = f"No plugin.yaml found in {plugin_dir}"
        raise ValueError(msg)

    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw.get("name"):
        msg = "plugin.yaml must contain at least a 'name' field"
        raise ValueError(msg)

    if output_path is None:
        output_path = plugin_dir.parent / f"{plugin_dir.name}{BUNDLE_EXTENSION}"

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for child in plugin_dir.rglob("*"):
            if child.is_file() and "__pycache__" not in child.parts:
                arcname = child.relative_to(plugin_dir)
                zf.write(child, arcname)

    file_count = sum(1 for _ in plugin_dir.rglob("*"))
    logger.info("Created bundle: %s (%d files)", output_path, file_count)
    return output_path


def detect_layout(directory: Path) -> str:
    """Classify a plugin directory layout: ``"dual"``, ``"legacy"``, or ``"unknown"``.

    Dual-format trees carry plugin.json at the root with the Hecate
    manifest inside the namespace directory; legacy trees carry
    plugin.yaml at the root.
    """
    if (directory / "plugin.json").is_file():
        return "dual"
    if (directory / "plugin.yaml").is_file():
        return "legacy"
    return "unknown"


def detect_bundle_layout(bundle_path: Path) -> str:
    """Layout of a ``.hecate-plugin`` ZIP without extracting it."""
    try:
        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return "unknown"
    roots = {n.replace("\\", "/").split("/")[0] for n in names}
    if "plugin.json" in roots:
        return "dual"
    if any(n.replace("\\", "/") == "plugin.yaml" or n.replace("\\", "/").endswith("/plugin.yaml") for n in names):
        return "legacy"
    return "unknown"


def emit_dual_format(
    plugin_dir: Path,
    output: Path | None = None,
    *,
    author: str | None = None,
    homepage: str | None = None,
    repository: str | None = None,
) -> Path:
    """Emit a dual-format Agent Plugins tree from a legacy plugin directory.

    Generates plugin.json from the plugin.yaml identity fields, relocates
    the manifest (minus name/version) into the namespace directory, and
    moves the Python payload with it. skills/ and mcp.json pass through
    untouched; the output is produced uniformly even with an empty open
    face. Returns the created directory (git-ready).
    """
    from hecate.core.plugin.dual_format import NAMESPACE_DIR_NAME, build_plugin_json

    manifest_path = plugin_dir / "plugin.yaml"
    if not manifest_path.is_file():
        msg = f"No plugin.yaml found in {plugin_dir}"
        raise ValueError(msg)
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw.get("name"):
        msg = "plugin.yaml must contain at least a 'name' field"
        raise ValueError(msg)

    out = output or plugin_dir.parent / f"{plugin_dir.name}.agentplugin"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    plugin_json = build_plugin_json(raw, author=author, homepage=homepage, repository=repository)
    (out / "plugin.json").write_text(json.dumps(plugin_json, indent=2), encoding="utf-8")

    ns = out / NAMESPACE_DIR_NAME
    ns.mkdir()
    ns_manifest = {k: v for k, v in raw.items() if k not in ("name", "version")}
    (ns / "plugin.yaml").write_text(yaml.safe_dump(ns_manifest, sort_keys=False), encoding="utf-8")

    for child in sorted(plugin_dir.iterdir()):
        if child.name in _DUAL_SKIP:
            continue
        dest = out / child.name if child.name in _OPEN_FACE_PASSTHROUGH else ns / child.name
        if child.is_dir():
            shutil.copytree(child, dest, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(child, dest)

    logger.info("Emitted dual-format package: %s", out)
    return out


def bundle_dual_format(tree_dir: Path, output_path: Path | None = None) -> Path:
    """ZIP a dual-format tree as ``.hecate-plugin`` transport.

    Unzipping the archive yields a valid Agent Plugins package; the ZIP
    itself remains installable by Hecate (transport only).
    """
    if detect_layout(tree_dir) != "dual":
        msg = f"Not a dual-format tree (no plugin.json at root): {tree_dir}"
        raise ValueError(msg)
    out = output_path or tree_dir.parent / f"{tree_dir.name}{BUNDLE_EXTENSION}"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for child in sorted(tree_dir.rglob("*")):
            if child.is_file() and "__pycache__" not in child.parts and ".git" not in child.parts:
                zf.write(child, child.relative_to(tree_dir))
    logger.info("Bundled dual-format package: %s", out)
    return out


def extract_bundle(bundle_path: Path, target_dir: Path) -> Path:
    """Extract a ``.hecate-plugin`` ZIP to *target_dir*.

    Returns the path to the extracted plugin directory.
    """
    if not validate_bundle(bundle_path):
        msg = f"Invalid bundle: {bundle_path}"
        raise ValueError(msg)

    target_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(bundle_path, "r") as zf:
        zf.extractall(target_dir)

    manifest_path = target_dir / "plugin.yaml"
    if not manifest_path.is_file():
        for child in target_dir.iterdir():
            if (child / "plugin.yaml").is_file():
                return child
        msg = f"Bundle extracted but no plugin.yaml found in {target_dir}"
        raise ValueError(msg)

    return target_dir
