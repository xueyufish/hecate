"""Plugin bundle packaging — create and extract .hecate-plugin ZIP archives."""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

BUNDLE_EXTENSION = ".hecate-plugin"


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
        output_path = Path(f"{plugin_dir.name}{BUNDLE_EXTENSION}")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for child in plugin_dir.rglob("*"):
            if child.is_file() and "__pycache__" not in child.parts:
                arcname = child.relative_to(plugin_dir)
                zf.write(child, arcname)

    file_count = sum(1 for _ in plugin_dir.rglob("*"))
    logger.info("Created bundle: %s (%d files)", output_path, file_count)
    return output_path


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
