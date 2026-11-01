"""Release-managed host layout: offline install, cold-start switch, rollback.

A release-managed host separates stable operator state from immutable
artifact content:

    <host>/
      runner.json            # operator-owned host config (stable)
      identity.json          # operator-owned trust material
      secrets/, evidence/    # operator-owned; referenced by relative path
      releases/<sha256>/     # digest-addressed artifact content (immutable)
        agent-manifest.json, artifact-signature.json, files/...
      staging/<uuid>/        # in-progress install; never selectable
      active.json            # activation pointer, committed atomically
      host.lock              # OS-level cross-process mutex

The startup entry (``hecate-runner --host``) consumes ``active.json`` and
runs the selected release against the host configuration. Activation and
rollback share the startup mutex and refuse while the runner holds the
lock. Every activation re-verifies the candidate under the CURRENT
trust policy and the full profile — a signature that passed when the
release was installed says nothing about now.

Rollback only re-selects an installed artifact: tasks, action receipts,
approval consumption, budgets, evidence and known revocations are never
rewound, and no database or run-state downgrade exists.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from hecate_runtime.manifest import ArtifactManifest, ManifestError, sha256_hex, validate_manifest_dict
from sqlalchemy.engine import make_url

from .artifact_signing import (
    ArtifactAdmissionError,
    ArtifactTrustPolicy,
    canonical_manifest_bytes,
    read_artifact_json,
    resolve_signature_path,
    verify_artifact_admission,
)
from .profile import Profile, ProfileError, load_profile

ACTIVE_POINTER_NAME = "active.json"
HOST_LOCK_NAME = "host.lock"
RELEASES_DIR = "releases"
STAGING_DIR = "staging"
POINTER_VERSION = 1
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ReleaseError(Exception):
    """Release management refusal; the operation did not take effect."""


@dataclass(frozen=True)
class ActiveSelection:
    """The committed activation pointer (recorded facts only)."""

    manifest_sha256: str
    previous: str | None
    generation: int
    durable_database: str | None


@dataclass(frozen=True)
class ReleaseInfo:
    """One installed release as recorded on disk."""

    manifest_sha256: str
    publisher_ref: str | None
    backend_type: str | None
    file_count: int
    active: bool


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
    """Cross-process mutex for startup and version switching.

    The lock is an OS-backed cooperative lock on ``host.lock``: a crash
    releases it with the process, so there is no stale-PID judgment and no
    "looks not running" heuristic. The serving runner holds it for its
    whole lifetime; activate and rollback must acquire it to touch the
    pointer, which is what makes switching a running host impossible.
    """

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
            raise ReleaseError(
                "host lock is held: the runner is serving or another management operation is in progress"
            ) from exc
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


def read_active_selection(host_dir: Path) -> ActiveSelection | None:
    """Read the committed pointer; a malformed pointer refuses (fail closed)."""

    pointer = _plain_child(host_dir, ACTIVE_POINTER_NAME)
    if not pointer.exists():
        return None
    try:
        data = read_artifact_json(pointer, "activation pointer")
    except ArtifactAdmissionError as exc:
        raise ReleaseError(f"activation pointer is unreadable; refusing to guess: {exc}") from exc
    unknown = data.keys() - {"pointer_version", "manifest_sha256", "previous", "generation", "durable_database"}
    if unknown or type(data.get("pointer_version")) is not int or data["pointer_version"] != POINTER_VERSION:
        raise ReleaseError(f"activation pointer has an unsupported shape: {sorted(data.keys())}")
    sha = data.get("manifest_sha256")
    previous = data.get("previous")
    generation = data.get("generation")
    durable_database = data.get("durable_database")
    if "durable_database" not in data or (durable_database is not None and not isinstance(durable_database, str)):
        raise ReleaseError("activation pointer durable database binding is unverifiable")
    if type(generation) is not int or generation < 1:
        raise ReleaseError("activation pointer generation must be a positive integer")
    if not isinstance(sha, str) or not _SHA256_RE.fullmatch(sha):
        raise ReleaseError("activation pointer manifest_sha256 must be a lowercase hex sha256")
    if previous is not None and (not isinstance(previous, str) or not _SHA256_RE.fullmatch(previous)):
        raise ReleaseError("activation pointer previous must be a sha256 or null")
    return ActiveSelection(
        manifest_sha256=sha, previous=previous, generation=generation, durable_database=durable_database
    )


def _commit_active_selection(
    host_dir: Path, manifest_sha256: str, previous: str | None, generation: int, durable_database: str | None
) -> None:
    """Atomically publish the pointer; a failure leaves the old one in place."""

    pointer = _plain_child(host_dir, ACTIVE_POINTER_NAME)
    document = {
        "pointer_version": POINTER_VERSION,
        "manifest_sha256": manifest_sha256,
        "previous": previous,
        "generation": generation,
        "durable_database": durable_database,
    }
    staged = pointer.with_name(f"{pointer.name}.{uuid.uuid4().hex}.tmp")
    try:
        with staged.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, pointer)
    except OSError as exc:
        raise ReleaseError(f"cannot commit activation pointer; previous selection retained: {exc}") from exc
    finally:
        if staged.exists():
            with contextlib.suppress(OSError):
                staged.unlink()


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


def list_releases(host_dir: Path) -> list[ReleaseInfo]:
    """Recorded facts about installed releases and the committed selection."""

    host_dir = require_initialized_host(host_dir)
    active = read_active_selection(host_dir)
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
                active=bool(active and active.manifest_sha256 == release_path.name),
            )
        )
    return infos


def _local_database(profile: Profile) -> Path | None:
    if profile.config.durable is None:
        return None
    try:
        url = make_url(profile.config.durable.database_url)
    except Exception as exc:
        raise ReleaseError("durable database configuration cannot be verified") from exc
    if url.drivername != "sqlite" or not url.database or url.query:
        raise ReleaseError("release-managed durable hosts support only an absolute local SQLite file")
    path = Path(url.database)
    if not path.is_absolute() or path.drive.startswith("\\\\"):
        raise ReleaseError("release-managed durable hosts require an absolute local SQLite file")
    if not path.is_file():
        raise ReleaseError("durable task state cannot be verified; an existing initialized ledger is required")
    return path.resolve()


def _database_binding(database: Path | None) -> str | None:
    return os.path.normcase(str(database)) if database else None


def _check_database_binding(profile: Profile, active: ActiveSelection | None) -> Path | None:
    database = _local_database(profile)
    if active is not None and active.durable_database != _database_binding(database):
        raise ReleaseError("host durable database binding changed; automatic data migration or reset is unsupported")
    return database


def ensure_durable_state_quiescent(database: Path | None, *, require_quiescence: bool = True) -> None:
    """Refuse switching while the host's durable ledger still carries work.

    Any non-terminal task — queued, running, waiting for input, approval or
    a timer, or parked for reconciliation — blocks the switch. When the
    state cannot be verified the switch is refused as well; the ledger is
    never cleared to let a switch through.
    """

    from hecate_durable.contracts.durable import TaskLifecycleState

    if database is None:
        return
    non_terminal = {
        TaskLifecycleState.QUEUED,
        TaskLifecycleState.RUNNING,
        TaskLifecycleState.WAITING_INPUT,
        TaskLifecycleState.WAITING_APPROVAL,
        TaskLifecycleState.RECONCILIATION_REQUIRED,
    }
    from hecate_durable.storage.inspection import read_sqlite_task_states

    try:
        states = read_sqlite_task_states(database)
    except Exception as exc:  # noqa: BLE001 — any failure means "state unverifiable"
        raise ReleaseError("durable task state cannot be verified; refusing release operation") from exc
    blocked = [state for state in states if state in non_terminal]
    if require_quiescence and blocked:
        raise ReleaseError("durable ledger still has non-terminal tasks; resolve or reconcile before switching")


def _verify_and_point(
    host_dir: Path, manifest_sha256: str, policy: ArtifactTrustPolicy, active: ActiveSelection | None
) -> Profile:
    """Shared activate/rollback body; caller holds the host lock."""

    target = release_dir_for(host_dir, manifest_sha256)
    if not target.is_dir():
        raise ReleaseError(f"release {manifest_sha256} is not installed")
    try:
        profile = load_profile(host_dir, artifact_dir=target, artifact_trust=policy)
    except ProfileError as exc:
        raise ReleaseError(f"candidate release does not pass the current gates: {exc}") from exc
    _require_release_identity(profile, manifest_sha256)
    database = _check_database_binding(profile, active)
    ensure_durable_state_quiescent(database)
    if active and active.manifest_sha256 == manifest_sha256:
        return profile  # already active; pointer unchanged
    _commit_active_selection(
        host_dir,
        manifest_sha256,
        previous=active.manifest_sha256 if active else None,
        generation=active.generation + 1 if active else 1,
        durable_database=_database_binding(database),
    )
    return profile


def _require_release_identity(profile: Profile, manifest_sha256: str) -> None:
    admission = profile.artifact_admission
    if admission is None or admission.manifest_sha256 != manifest_sha256:
        raise ReleaseError("release directory identity does not match the verified manifest digest")


def _checked_selection(host_dir: Path, expected_generation: int) -> ActiveSelection | None:
    if type(expected_generation) is not int or expected_generation < 0:
        raise ReleaseError("expected generation must be a non-negative integer")
    active = read_active_selection(host_dir)
    if (active.generation if active else 0) != expected_generation:
        raise ReleaseError("selection generation conflict; refresh status before switching")
    return active


def activate_release(
    host_dir: Path, manifest_sha256: str, policy: ArtifactTrustPolicy, *, expected_generation: int
) -> Profile:
    """Select an installed release for the next cold start (mutex-guarded)."""

    host_dir = require_initialized_host(host_dir)
    with HostLock(host_dir):
        active = _checked_selection(host_dir, expected_generation)
        return _verify_and_point(host_dir, manifest_sha256, policy, active)


def rollback_release(host_dir: Path, policy: ArtifactTrustPolicy, *, expected_generation: int) -> Profile:
    """Re-select the previously active release under the CURRENT policy."""

    host_dir = require_initialized_host(host_dir)
    with HostLock(host_dir):
        active = _checked_selection(host_dir, expected_generation)
        if active is None:
            raise ReleaseError("no active release recorded; nothing to roll back")
        if active.previous is None:
            raise ReleaseError(f"release {active.manifest_sha256} has no recorded previous version")
        return _verify_and_point(host_dir, active.previous, policy, active)


def load_host_selection(host_dir: Path, policy: ArtifactTrustPolicy) -> tuple[Profile, HostLock]:
    """Startup path for a release-managed host.

    Acquires the host lock (held for the runner's lifetime) and loads the
    committed selection under the CURRENT trust policy — the pointer alone
    never authorizes a start.
    """

    host_dir = require_initialized_host(host_dir)
    lock = HostLock(host_dir)
    lock.acquire()
    try:
        active = read_active_selection(host_dir)
        if active is None:
            raise ReleaseError(f"no active release selected in {host_dir}; run 'hecate-runner-release activate' first")
        target = release_dir_for(host_dir, active.manifest_sha256)
        if not target.is_dir():
            raise ReleaseError(f"active release {active.manifest_sha256} is missing from releases/")
        profile = load_profile(host_dir, artifact_dir=target, artifact_trust=policy)
        _require_release_identity(profile, active.manifest_sha256)
        database = _check_database_binding(profile, active)
        ensure_durable_state_quiescent(database, require_quiescence=False)
    except Exception:
        lock.release()
        raise
    return profile, lock


def _print_release_line(info: ReleaseInfo) -> None:
    marker = "*" if info.active else " "
    publisher = info.publisher_ref if info.publisher_ref else "unknown"
    backend = info.backend_type if info.backend_type else "unknown"
    print(f"{marker} {info.manifest_sha256}  publisher={publisher} backend={backend} files={info.file_count}")


def main(argv: list[str] | None = None) -> int:
    """Run local release commands with explicit refusal exit codes."""

    parser = argparse.ArgumentParser(
        prog="hecate-runner-release",
        description="Offline install, version switching and rollback for signed runner artifacts",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_host(p: argparse.ArgumentParser) -> None:
        p.add_argument("--host", required=True, type=Path, help="release-managed host directory")

    install = sub.add_parser("install", help="verify and install a signed artifact into the host")
    add_host(install)
    install.add_argument(
        "--artifact", required=True, type=Path, help="artifact directory (manifest, signature, files/)"
    )
    install.add_argument("--trust-policy", required=True, type=Path, dest="trust_policy")
    install.set_defaults(func=_cmd_install)

    for name in ("list", "status"):
        list_cmd = sub.add_parser(name, help="list installed releases and the committed selection")
        add_host(list_cmd)
        list_cmd.set_defaults(func=_cmd_list)

    activate = sub.add_parser("activate", help="select an installed release for the next cold start")
    add_host(activate)
    activate.add_argument("--release", required=True, help="manifest sha256 of the installed release")
    activate.add_argument("--trust-policy", required=True, type=Path, dest="trust_policy")
    activate.add_argument(
        "--expected-generation", required=True, type=int, help="generation read from status; 0 before first activation"
    )
    activate.set_defaults(func=_cmd_activate)

    rollback = sub.add_parser("rollback", help="re-select the previously active release")
    add_host(rollback)
    rollback.add_argument("--trust-policy", required=True, type=Path, dest="trust_policy")
    rollback.add_argument("--expected-generation", required=True, type=int, help="generation read from status")
    rollback.set_defaults(func=_cmd_rollback)

    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except (ReleaseError, ProfileError, OSError) as exc:
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
    manifest_sha256 = install_release(host_dir, Path(args.artifact), policy)
    print(f"hecate-runner-release: installed release {manifest_sha256}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    active = read_active_selection(require_initialized_host(Path(args.host).resolve()))
    print("active: " + (active.manifest_sha256 if active else "none"))
    print(f"generation: {active.generation if active else 0}")
    infos = list_releases(Path(args.host))
    if not infos:
        print("(no releases installed)")
    for info in infos:
        _print_release_line(info)
    print("(* marks the committed selection; listing reads recorded facts only)")
    return 0


def _cmd_activate(args: argparse.Namespace) -> int:
    from .artifact_signing import load_trust_policy

    host_dir = Path(args.host)
    try:
        policy = load_trust_policy(Path(args.trust_policy))
    except ArtifactAdmissionError as exc:
        print(f"hecate-runner-release: artifact trust policy error: {exc}", file=sys.stderr)
        return 2
    activate_release(host_dir, args.release, policy, expected_generation=args.expected_generation)
    print(f"hecate-runner-release: activated {args.release} for the next cold start")
    return 0


def _cmd_rollback(args: argparse.Namespace) -> int:
    from .artifact_signing import load_trust_policy

    host_dir = Path(args.host)
    try:
        policy = load_trust_policy(Path(args.trust_policy))
    except ArtifactAdmissionError as exc:
        print(f"hecate-runner-release: artifact trust policy error: {exc}", file=sys.stderr)
        return 2
    profile = rollback_release(host_dir, policy, expected_generation=args.expected_generation)
    print(f"hecate-runner-release: rolled back to {profile.artifact_directory.name} for the next cold start")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
