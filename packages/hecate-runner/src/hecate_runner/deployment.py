"""Bind a signed artifact to a verified offline environment at cold start."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import sysconfig
from dataclasses import dataclass
from importlib.metadata import distributions
from pathlib import Path

DEPLOYMENT_FORMAT_VERSION = 1
RECEIPT_FORMAT_VERSION = 1
RECEIPT_FILENAME = "hecate-install-receipt.json"
_SHA256 = re.compile(r"[0-9a-f]{64}")
# The venv bootstrap seeds its own tooling outside the dependency lock;
# the receipt never lists it and the installed-set comparison skips it.
_VENV_SEEDED = frozenset({"pip"})
_MAX_DOCUMENT_BYTES = 1024 * 1024


class DeploymentError(Exception):
    """The deployment composition does not match what is about to run."""


def canonical_name(name: str) -> str:
    """Normalize a distribution name the way packaging metadata does."""

    return re.sub(r"[-_.]+", "-", name).lower()


def _digest_without(document: dict, embedded_key: str) -> str:
    payload = {key: value for key, value in document.items() if key != embedded_key}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _read_verified_document(path: Path, label: str, digest_field: str, format_version: int) -> tuple[dict, str]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(_MAX_DOCUMENT_BYTES + 1)
        if len(raw) > _MAX_DOCUMENT_BYTES:
            raise ValueError("document exceeds size limit")
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_fields)
        if not isinstance(document, dict):
            raise ValueError("expected a JSON object")
        calculated = _digest_without(document, digest_field)
    except (OSError, ValueError, RecursionError) as exc:
        raise DeploymentError(f"{label} is missing or unreadable: {exc}") from exc
    if document.get(digest_field) != calculated:
        raise DeploymentError(f"{label} digest does not match its own content")
    version = document.get("format_version")
    if type(version) is not int or version != format_version:
        raise DeploymentError(f"unsupported {label} format_version")
    return document, calculated


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise DeploymentError(f"{label} must be a lowercase hex sha256")
    return value


@dataclass(frozen=True)
class InstallReceipt:
    """The recorded facts of one verified offline installation."""

    environment_manifest_sha256: str
    digest: str
    packages: dict[str, str]
    venv_path_sha256: str
    python_target: str
    python_implementation: str
    python_abi: str | None


def read_install_receipt(path: Path) -> InstallReceipt:
    """Read and verify the receipt at the specified path."""
    receipt, digest = _read_verified_document(path, "install receipt", "receipt_sha256", RECEIPT_FORMAT_VERSION)
    packages = receipt.get("packages")
    if (
        not isinstance(packages, dict)
        or not packages
        or not all(
            name and canonical_name(name) == name and isinstance(version, str) and version
            for name, version in packages.items()
        )
    ):
        raise DeploymentError("install receipt packages must be a canonical name-to-version object")
    target, implementation, abi = (
        receipt.get("python_target"),
        receipt.get("python_implementation"),
        receipt.get("python_abi"),
    )
    if (
        not isinstance(target, str)
        or not isinstance(implementation, str)
        or (abi is not None and not isinstance(abi, str))
    ):
        raise DeploymentError("install receipt Python target is malformed")
    return InstallReceipt(
        environment_manifest_sha256=_require_digest(
            receipt.get("environment_manifest_sha256"), "receipt environment digest"
        ),
        digest=digest,
        packages=packages,
        venv_path_sha256=_require_digest(receipt.get("venv_path_sha256"), "receipt environment path digest"),
        python_target=target,
        python_implementation=implementation,
        python_abi=abi,
    )


def installed_distribution_set(venv_root: Path) -> dict[str, str]:
    """Read visible distribution metadata, refusing foreign or ambiguous entries."""
    found: dict[str, str] = {}
    try:
        for distribution in distributions():
            name = distribution.metadata.get("Name")
            version = distribution.version
            if not name or not version:
                raise DeploymentError("installed distribution metadata has no name or version")
            canonical = canonical_name(name)
            location = Path(str(distribution.locate_file(""))).resolve()
            if not location.is_relative_to(venv_root):
                raise DeploymentError(f"installed distribution is outside the verified environment: {canonical!r}")
            if canonical in found:
                raise DeploymentError(f"duplicate installed distribution: {canonical!r}")
            found[canonical] = version
    except (OSError, ValueError, RuntimeError) as exc:
        raise DeploymentError(f"installed distribution metadata is unreadable: {exc}") from exc
    return found


def verify_installed_set(receipt: InstallReceipt, venv_root: Path) -> None:
    """Compare the interpreter's distribution set with the receipt."""
    actual = installed_distribution_set(venv_root)
    for package in _VENV_SEEDED - receipt.packages.keys():
        actual.pop(package, None)
    if actual != receipt.packages:
        missing = sorted(set(receipt.packages) - set(actual))
        extra = sorted(set(actual) - set(receipt.packages))
        drifted = sorted(name for name in set(actual) & set(receipt.packages) if actual[name] != receipt.packages[name])
        raise DeploymentError(
            "installed environment drifted from the install receipt "
            f"(missing: {missing}, extra: {extra}, version drift: {drifted})"
        )


@dataclass(frozen=True)
class DeploymentManifest:
    """The loaded composition binding and its verified facts."""

    artifact_manifest_sha256: str
    publisher_ref: str
    environment_manifest_sha256: str
    receipt_sha256: str


def generate_deployment_manifest(
    *,
    artifact_manifest_sha256: str,
    publisher_ref: str,
    environment_manifest_sha256: str,
    receipt_sha256: str,
) -> dict:
    """Build the composition manifest document with its embedded digest."""

    _require_digest(artifact_manifest_sha256, "artifact manifest digest")
    _require_digest(environment_manifest_sha256, "environment manifest digest")
    _require_digest(receipt_sha256, "receipt digest")
    if not isinstance(publisher_ref, str) or not publisher_ref.isprintable() or publisher_ref != publisher_ref.strip():
        raise DeploymentError("publisher_ref must be a non-empty printable identifier")
    manifest = {
        "format_version": DEPLOYMENT_FORMAT_VERSION,
        "artifact": {
            "manifest_sha256": artifact_manifest_sha256,
            "publisher_ref": publisher_ref,
        },
        "environment": {
            "manifest_sha256": environment_manifest_sha256,
            "receipt_sha256": receipt_sha256,
        },
    }
    manifest["deployment_sha256"] = _digest_without(manifest, "deployment_sha256")
    return manifest


def load_deployment_manifest(manifest_path: Path, expected_digest: str) -> DeploymentManifest:
    """Verify the composition and its independently received digest."""
    manifest, recorded = _read_verified_document(
        manifest_path, "deployment manifest", "deployment_sha256", DEPLOYMENT_FORMAT_VERSION
    )
    expected = expected_digest.strip().lower()
    if not _SHA256.fullmatch(expected) or not hmac.compare_digest(recorded, expected):
        raise DeploymentError("deployment digest does not match the operator-provided digest")
    artifact = manifest.get("artifact")
    environment = manifest.get("environment")
    if not isinstance(artifact, dict) or not isinstance(environment, dict):
        raise DeploymentError("deployment manifest must carry artifact and environment objects")
    publisher = artifact.get("publisher_ref")
    if not isinstance(publisher, str) or not publisher.isprintable() or publisher != publisher.strip():
        raise DeploymentError("deployment publisher_ref must be a non-empty printable identifier")
    return DeploymentManifest(
        artifact_manifest_sha256=_require_digest(artifact.get("manifest_sha256"), "artifact manifest digest"),
        publisher_ref=publisher,
        environment_manifest_sha256=_require_digest(environment.get("manifest_sha256"), "environment manifest digest"),
        receipt_sha256=_require_digest(environment.get("receipt_sha256"), "receipt digest"),
    )


def verify_deployment(
    manifest: DeploymentManifest,
    *,
    active_artifact_manifest_sha256: str,
    admitted_publisher_ref: str,
    admitted_artifact_manifest_sha256: str,
    venv_root: Path,
) -> None:
    """Check the composition against what is actually about to run.

    The artifact binding is checked twice on purpose: the pointer must
    select the composed release (catching an activate after the manifest
    was written), and the policy-verified admission must name the same
    digest and publisher (catching a manifest that names a release the
    current policy no longer backs). The environment side loads the
    receipt from the running venv, matches it to the composed environment
    and receipt digests, and compares the actual installed package set.
    """

    if manifest.artifact_manifest_sha256 != active_artifact_manifest_sha256:
        raise DeploymentError(
            f"deployment composes artifact {manifest.artifact_manifest_sha256[:12]}.. but the host has "
            f"{active_artifact_manifest_sha256[:12]}.. active; activate the composed release or generate a "
            "manifest for the selected one"
        )
    if manifest.artifact_manifest_sha256 != admitted_artifact_manifest_sha256 or (
        manifest.publisher_ref != admitted_publisher_ref
    ):
        raise DeploymentError(
            "deployment artifact binding disagrees with the policy-verified admission "
            f"(manifest: {manifest.publisher_ref!r}/{manifest.artifact_manifest_sha256[:12]}.., "
            f"admitted: {admitted_publisher_ref!r}/{admitted_artifact_manifest_sha256[:12]}..)"
        )
    try:
        venv_root = venv_root.resolve()
    except (OSError, RuntimeError) as exc:
        raise DeploymentError("cannot resolve the running environment") from exc
    receipt_path = venv_root / RECEIPT_FILENAME
    if receipt_path.is_symlink():
        raise DeploymentError("install receipt must be a local file in the running environment")
    receipt = read_install_receipt(receipt_path)
    if manifest.environment_manifest_sha256 != receipt.environment_manifest_sha256:
        raise DeploymentError(
            "deployment composes a different environment than this interpreter was installed from "
            f"(manifest: {manifest.environment_manifest_sha256[:12]}.., receipt: "
            f"{receipt.environment_manifest_sha256[:12]}..)"
        )
    if not hmac.compare_digest(manifest.receipt_sha256, receipt.digest):
        raise DeploymentError("deployment receipt digest does not match the installed receipt")
    path_digest = hashlib.sha256(os.fsencode(os.path.normcase(str(venv_root)))).hexdigest()
    if path_digest != receipt.venv_path_sha256:
        raise DeploymentError("install receipt belongs to a different environment path")
    if (
        receipt.python_target != f"{sys.version_info.major}.{sys.version_info.minor}"
        or receipt.python_implementation != sys.implementation.name
        or receipt.python_abi != sysconfig.get_config_var("SOABI")
    ):
        raise DeploymentError("install receipt targets a different Python interpreter")
    verify_installed_set(receipt, venv_root)


def generate_command(argv: list[str] | None = None) -> int:
    """CLI: compose the manifest from the two identities plus the receipt."""

    parser = argparse.ArgumentParser(
        prog="hecate-runner-deployment",
        description="Compose the deployment manifest binding a signed artifact to a verified environment",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate", help="write deployment.json from the two identities and the receipt")
    generate.add_argument("--artifact-sha256", required=True, dest="artifact_sha256", help="signed manifest sha256")
    generate.add_argument("--publisher-ref", required=True, help="publisher the artifact was admitted under")
    generate.add_argument(
        "--environment-manifest",
        required=True,
        type=Path,
        dest="environment_manifest",
        help="path to the delivery's environment.json",
    )
    generate.add_argument(
        "--receipt", required=True, type=Path, help="path to the installed environment's hecate-install-receipt.json"
    )
    generate.add_argument("--out", required=True, type=Path, help="deployment manifest output path (must not exist)")
    args = parser.parse_args(argv)

    try:
        _, environment_digest = _read_verified_document(
            args.environment_manifest, "environment manifest", "manifest_sha256", 1
        )
        receipt = read_install_receipt(args.receipt)
        if receipt.environment_manifest_sha256 != environment_digest:
            raise DeploymentError("environment manifest and install receipt disagree")
        manifest = generate_deployment_manifest(
            artifact_manifest_sha256=args.artifact_sha256.strip().lower(),
            publisher_ref=args.publisher_ref,
            environment_manifest_sha256=environment_digest,
            receipt_sha256=receipt.digest,
        )
        with args.out.open("x", encoding="utf-8") as output:
            output.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    except FileExistsError:
        print(f"hecate-runner-deployment: output already exists: {args.out}", file=sys.stderr)
        return 2
    except (DeploymentError, OSError) as exc:
        print(f"hecate-runner-deployment: {exc}", file=sys.stderr)
        return 2
    print(f"hecate-runner-deployment: wrote {args.out}")
    print(f"hecate-runner-deployment: deployment_sha256 {manifest['deployment_sha256']}")
    print("Move this digest to the serving machine over an independent trusted channel;")
    print("cold start refuses a composition it cannot correlate to your copy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(generate_command())
