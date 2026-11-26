"""Verify, package, and install signed Runner artifacts.

The operator's profile contains configuration, secrets, and evidence.
Immutable artifacts are installed under ``releases/<sha256>/`` and selected
explicitly by the runner at startup.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from hecate_runtime.manifest import ArtifactManifest, ManifestError, sha256_hex, validate_manifest_dict

from .artifact_signing import (
    ArtifactAdmissionError,
    ArtifactTrustPolicy,
    canonical_manifest_bytes,
    read_artifact_json,
    resolve_signature_path,
    verify_artifact_admission,
)

HOST_LOCK_NAME = "host.lock"
RELEASES_DIR = "releases"
STAGING_DIR = "staging"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ReleaseError(Exception):
    """Release management refusal; the operation did not take effect."""


@dataclass(frozen=True)
class ReleaseInfo:
    """One installed release as recorded on disk."""

    manifest_sha256: str
    publisher_ref: str | None
    backend_type: str | None
    file_count: int


def _release_root(host_dir: Path) -> Path:
    return _plain_child(host_dir, RELEASES_DIR)


def _plain_child(root: Path, relative: str) -> Path:
    """Constrain every component before copying, publishing or deleting content."""

    if (
        "\\" in relative
        or ":" in relative
        or "\x00" in relative
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ReleaseError("release path must be a portable relative path")
    root = root.resolve()
    current = root
    for part in relative.split("/"):
        current = current / part
        if current.is_symlink() or current.is_junction():
            raise ReleaseError(f"release path must not traverse a symlink or junction: {relative!r}")
    if not current.resolve().is_relative_to(root):
        raise ReleaseError("release path escapes its root")
    return current


def release_dir_for(host_dir: Path, manifest_sha256: str) -> Path:
    """Resolve a digest-addressed release without following layout links."""

    if not isinstance(manifest_sha256, str) or not _SHA256_RE.fullmatch(manifest_sha256):
        raise ReleaseError(f"release id must be a lowercase hex sha256: {manifest_sha256!r}")
    return _plain_child(host_dir, f"{RELEASES_DIR}/{manifest_sha256}")


def require_initialized_host(host_dir: Path) -> Path:
    """The host must already carry its operator-owned configuration."""

    host_dir = host_dir.resolve()
    if not host_dir.is_dir():
        raise ReleaseError(f"host directory does not exist: {host_dir}")
    for name in ("runner.json", "identity.json"):
        if not (host_dir / name).is_file():
            raise ReleaseError(f"host is not initialized: missing {name} in {host_dir}")
    return host_dir


class HostLock:
    """Prevent simultaneous serving processes for an explicit artifact profile."""

    def __init__(self, host_dir: Path) -> None:
        self._path = _plain_child(host_dir, HOST_LOCK_NAME)
        self._fd: int | None = None

    def acquire(self) -> None:
        """Acquire the host mutex without waiting for a serving process."""

        fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise ReleaseError("host lock is held by another runner") from exc
        self._fd = fd

    def release(self) -> None:
        """Release an acquired descriptor; repeated cleanup is harmless."""

        if self._fd is None:
            return
        try:
            os.lseek(self._fd, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass  # the OS drops the lock with the descriptor anyway
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> HostLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def _copy_whitelisted_artifact(source: Path, staging: Path) -> dict:
    """Copy manifest, signature envelope and listed files; refuse link bypass.

    Only whitelisted content is copied. Source files and directories that
    are symlinks are refused — the installed release must be plain content.
    The staged bytes (not the source bytes) are the verification target.
    """

    if source.is_symlink() or source.is_junction():
        raise ReleaseError("artifact source must not be a symlink or junction")
    source = source.resolve()
    if not source.is_dir():
        raise ReleaseError(f"artifact source directory does not exist: {source}")
    manifest_path = _plain_child(source, "agent-manifest.json")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ReleaseError("artifact source must contain a plain agent-manifest.json")
    raw = read_artifact_json(manifest_path, "agent manifest")
    try:
        manifest = validate_manifest_dict(raw)
    except ManifestError as exc:
        raise ReleaseError(f"artifact manifest is invalid: {exc}") from exc
    signature_ref = manifest.signature_ref
    if not isinstance(signature_ref, str) or not signature_ref:
        raise ReleaseError("artifact manifest declares no signature_ref; only signed artifacts install")
    try:
        envelope_source = resolve_signature_path(source, signature_ref)
    except ArtifactAdmissionError as exc:
        raise ReleaseError(f"artifact admission refused: {exc}") from exc

    (staging / "files").mkdir(parents=True, exist_ok=False)
    for entry in manifest.files:
        relative = entry.path
        source_file = _plain_child(source, f"files/{relative}")
        target = _plain_child(staging, f"files/{relative}")
        if not source_file.is_file():
            raise ReleaseError(f"artifact file missing from source: files/{relative!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, target)
    shutil.copyfile(manifest_path, staging / "agent-manifest.json")
    envelope_target = _plain_child(staging, signature_ref)
    envelope_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(envelope_source, envelope_target)
    return raw


def _verify_staged_content(staging: Path) -> dict:
    """Verify the staged bytes: digests, then signature, then compatibility."""

    raw = read_artifact_json(staging / "agent-manifest.json", "staged agent manifest")
    try:
        manifest = validate_manifest_dict(raw)
    except ManifestError as exc:
        raise ReleaseError(f"staged manifest is invalid: {exc}") from exc
    for entry in manifest.files:
        staged_file = _plain_child(staging, f"files/{entry.path}")
        if staged_file.is_symlink() or not staged_file.is_file():
            raise ReleaseError(f"staged artifact file missing: files/{entry.path!r}")
        content = staged_file.read_bytes()
        if sha256_hex(content) != entry.sha256 or len(content) != entry.size:
            raise ReleaseError(f"staged artifact content does not match the manifest: files/{entry.path!r}")
    return raw


def verify_manifest_compat(manifest: object) -> None:
    """The manifest-level preview compatibility gate (shared with startup)."""

    if not isinstance(manifest, ArtifactManifest):
        raise ReleaseError("compatibility check requires a parsed manifest")
    if manifest.backend_type != "pregel" or manifest.backend_compat_version != "0.1":
        raise ReleaseError("preview supports only backend_type='pregel' with backend_compat_version='0.1'")
    if manifest.contract_version != "0.1":
        raise ReleaseError("preview supports only contract_version='0.1'")
    supported = {"submit", "read_events_cursor", "cancel", "local_evidence"}
    if set(manifest.required_capabilities) - supported:
        raise ReleaseError("manifest requires unsupported capabilities")


def _existing_release_matches(target: Path, staged_raw: dict, policy: ArtifactTrustPolicy) -> bool:
    """A published release with this digest must carry identical content."""

    try:
        existing_raw = _verify_staged_content(target)
        verified = verify_artifact_admission(existing_raw, target, policy)
    except (ArtifactAdmissionError, ReleaseError, OSError):
        return False
    if verified.manifest_sha256 != target.name:
        return False
    if canonical_manifest_bytes(existing_raw) != canonical_manifest_bytes(staged_raw):
        return False
    for entry in staged_raw.get("files", []):
        path = entry.get("path")
        existing_file = _plain_child(target, f"files/{path}")
        if not existing_file.is_file() or existing_file.is_symlink():
            return False
        content = existing_file.read_bytes()
        if sha256_hex(content) != entry.get("sha256") or len(content) != entry.get("size"):
            return False
    return True


def install_release(host_dir: Path, artifact_source: Path, policy: ArtifactTrustPolicy) -> str:
    """Install one signed artifact; returns the release id (manifest sha256).

    The artifact is copied into staging from whitelisted content only,
    verified there (digests, signature under the CURRENT policy, manifest
    compatibility) and published atomically. A crash at any point leaves,
    at worst, an unusable staging directory — never a selectable release.
    Re-installing the identical artifact is idempotent; content that hashes
    to an existing release id but differs is refused.
    """

    host_dir = require_initialized_host(host_dir)
    staging = _plain_child(host_dir, f"{STAGING_DIR}/{uuid.uuid4().hex}")
    try:
        staging.parent.mkdir(parents=True, exist_ok=True)
        _copy_whitelisted_artifact(artifact_source, staging)
        raw = _verify_staged_content(staging)
        try:
            verify_artifact_admission(raw, host_dir, policy, artifact_dir=staging)
        except ArtifactAdmissionError as exc:
            raise ReleaseError(f"artifact admission refused: {exc}") from exc

        verify_manifest_compat(validate_manifest_dict(raw))
        manifest_sha256 = sha256_hex(canonical_manifest_bytes(raw))
        target = release_dir_for(host_dir, manifest_sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if _existing_release_matches(target, raw, policy):
                return manifest_sha256
            raise ReleaseError(
                f"release {manifest_sha256} already exists with different content; refusing to replace it"
            )
        try:
            os.rename(staging, target)
        except OSError:
            if target.exists():
                # A concurrent install published the same release first.
                if _existing_release_matches(target, raw, policy):
                    return manifest_sha256
                raise ReleaseError(
                    f"release {manifest_sha256} already exists with different content; refusing to replace it"
                ) from None
            raise ReleaseError(f"cannot publish release directory: {target}") from None
        return manifest_sha256
    except (ArtifactAdmissionError, ManifestError, OSError) as exc:
        raise ReleaseError(f"cannot install artifact: {exc}") from exc
    finally:
        # Re-check the resolved target immediately before recursive cleanup.
        # A replaced layout link is left untouched rather than followed.
        with contextlib.suppress(ReleaseError):
            checked = _plain_child(host_dir, f"{STAGING_DIR}/{staging.name}")
            shutil.rmtree(checked, ignore_errors=True)


def install_bundle(host_dir: Path, bundle_path: Path, policy: ArtifactTrustPolicy) -> str:
    """Install one signed artifact from its standard bundle archive.

    The bundle is read with hard bounds into the host's isolated staging
    area and then runs through the exact directory-install gates and
    publish path; nothing is written outside staging before verification.
    """

    from .bundle import extract_bundle

    host_dir = require_initialized_host(host_dir)
    extracted = _plain_child(host_dir, f"{STAGING_DIR}/bundle-{uuid.uuid4().hex}")
    extracted.parent.mkdir(parents=True, exist_ok=True)
    extracted.mkdir()
    try:
        extract_bundle(bundle_path, extracted)
        return install_release(host_dir, extracted, policy)
    finally:
        with contextlib.suppress(ReleaseError):
            checked = _plain_child(host_dir, f"{STAGING_DIR}/{extracted.name}")
            shutil.rmtree(checked, ignore_errors=True)


def list_releases(host_dir: Path) -> list[ReleaseInfo]:
    """Read metadata about installed releases."""

    host_dir = require_initialized_host(host_dir)
    infos: list[ReleaseInfo] = []
    for release_path in sorted(_release_root(host_dir).iterdir()) if _release_root(host_dir).is_dir() else []:
        if not release_path.is_dir() or not _SHA256_RE.fullmatch(release_path.name):
            continue
        manifest_path = release_path / "agent-manifest.json"
        publisher: str | None = None
        backend: str | None = None
        file_count = 0
        try:
            raw = read_artifact_json(manifest_path, "agent manifest")
            publisher = raw.get("publisher_ref") if isinstance(raw.get("publisher_ref"), str) else None
            backend = raw.get("backend_type") if isinstance(raw.get("backend_type"), str) else None
            files = raw.get("files")
            file_count = len(files) if isinstance(files, list) else 0
        except ArtifactAdmissionError:
            pass  # recorded as an unreadable release with zero metadata
        infos.append(
            ReleaseInfo(
                manifest_sha256=release_path.name,
                publisher_ref=publisher,
                backend_type=backend,
                file_count=file_count,
            )
        )
    return infos


def _print_release_line(info: ReleaseInfo) -> None:
    publisher = info.publisher_ref if info.publisher_ref else "unknown"
    backend = info.backend_type if info.backend_type else "unknown"
    print(f"{info.manifest_sha256}  publisher={publisher} backend={backend} files={info.file_count}")


def main(argv: list[str] | None = None) -> int:
    """Run local release commands with explicit refusal exit codes."""

    parser = argparse.ArgumentParser(
        prog="hecate-runner-release",
        description="Package and install signed Runner artifacts",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_host(p: argparse.ArgumentParser) -> None:
        p.add_argument("--host", required=True, type=Path, help="Runner profile directory")

    install = sub.add_parser("install", help="verify and install a signed artifact into the host")
    add_host(install)
    install_source = install.add_mutually_exclusive_group(required=True)
    install_source.add_argument("--artifact", type=Path, help="artifact directory (manifest, signature, files/)")
    install_source.add_argument(
        "--bundle", type=Path, help="standard bundle archive (deterministic tar.gz from 'pack')"
    )
    install.add_argument("--trust-policy", required=True, type=Path, dest="trust_policy")
    install.set_defaults(func=_cmd_install)

    pack = sub.add_parser("pack", help="write the deterministic bundle archive of one verified artifact")
    pack.add_argument("--artifact", required=True, type=Path, help="artifact directory (manifest, signature, files/)")
    pack.add_argument("--out", required=True, type=Path, help="bundle output path (must not exist)")
    pack.add_argument("--trust-policy", required=True, type=Path, dest="trust_policy")
    pack.set_defaults(func=_cmd_pack)

    for name in ("list", "status"):
        list_cmd = sub.add_parser(name, help="list installed artifacts")
        add_host(list_cmd)
        list_cmd.set_defaults(func=_cmd_list)

    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except (ReleaseError, OSError) as exc:
        print(f"hecate-runner-release: {exc}", file=sys.stderr)
        return 2


def _cmd_install(args: argparse.Namespace) -> int:
    from .artifact_signing import load_trust_policy

    host_dir = Path(args.host)
    try:
        policy = load_trust_policy(Path(args.trust_policy))
    except ArtifactAdmissionError as exc:
        print(f"hecate-runner-release: artifact trust policy error: {exc}", file=sys.stderr)
        return 2
    if args.bundle is not None:
        manifest_sha256 = install_bundle(host_dir, Path(args.bundle), policy)
    else:
        manifest_sha256 = install_release(host_dir, Path(args.artifact), policy)
    print(f"hecate-runner-release: installed release {manifest_sha256}")
    return 0


def _cmd_pack(args: argparse.Namespace) -> int:
    from .artifact_signing import load_trust_policy
    from .bundle import pack_bundle

    try:
        policy = load_trust_policy(Path(args.trust_policy))
    except ArtifactAdmissionError as exc:
        print(f"hecate-runner-release: artifact trust policy error: {exc}", file=sys.stderr)
        return 2
    manifest_sha256 = pack_bundle(Path(args.artifact), Path(args.out), policy)
    print(f"hecate-runner-release: packed release {manifest_sha256} into {args.out}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    infos = list_releases(Path(args.host))
    if not infos:
        print("(no releases installed)")
    for info in infos:
        _print_release_line(info)
    return 0


if __name__ == "__main__":
    # Bundle imports this module's helpers and error type. Dispatch through
    # its canonical import so -m does not create incompatible exception types.
    from .release import main as release_main

    raise SystemExit(release_main())
