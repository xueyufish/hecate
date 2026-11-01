"""Standard archive bundling for signed runner artifacts.

A bundle is a deterministic tar.gz that transports exactly the signed
artifact layout the release manager already installs: ``agent-manifest.json``,
the signature envelope its ``signature_ref`` names, and the manifest-listed
``files/`` content — nothing else. Packing verifies the artifact under the
current trust policy first and writes fixed member order and metadata, so
the same artifact always produces the same bytes. Installing a bundle
re-reads it with hard bounds (member count, per-member and total size),
extracts only whitelisted members into the host's isolated staging area and
then reuses the standard directory-install gates unchanged.

The bundle is a transport format only. It does not carry host
configuration, identity material, secrets or run data, executes no install
scripts, and downloads nothing; Runtime payload-archive semantics
(``verify_archive``) are untouched.
"""

from __future__ import annotations

import gzip
import os
import shutil
import tarfile
import tempfile
import uuid
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from hecate_runtime.manifest import ManifestError, sha256_hex, validate_manifest_dict

from .artifact_signing import (
    ArtifactAdmissionError,
    ArtifactTrustPolicy,
    canonical_manifest_bytes,
    read_artifact_json,
    verify_artifact_admission,
)
from .release import (
    ReleaseError,
    _plain_child,
    _verify_staged_content,
    verify_manifest_compat,
)

# Extraction bounds. Tests shrink these to exercise the refusal paths
# without allocating real resources.
BUNDLE_MAX_MEMBERS = 512
BUNDLE_MAX_FILE_BYTES = 64 * 1024 * 1024
BUNDLE_MAX_TOTAL_BYTES = 512 * 1024 * 1024
BUNDLE_MAX_METADATA_BYTES = 16 * 1024
BUNDLE_MAX_JSON_BYTES = 1024 * 1024
BUNDLE_MAX_TRAILER_BYTES = tarfile.RECORDSIZE

MANIFEST_MEMBER = "agent-manifest.json"

# Windows path aliases that silently collide on a case-insensitive
# filesystem: reserved device names (in any segment) and trailing dots or
# spaces, which Windows strips from the effective name.
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(0, 10)),
    *(f"lpt{index}" for index in range(0, 10)),
}
_REGULAR_TYPES = (tarfile.REGTYPE, tarfile.AREGTYPE)
_METADATA_TYPES = (tarfile.GNUTYPE_LONGNAME, tarfile.XHDTYPE)


def _validate_member_name(name: str) -> None:
    """Reject anything that is not a plain portable relative path."""

    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or ":" in name
        or "\x00" in name
        or any(part in {"", ".", ".."} for part in name.split("/"))
    ):
        raise ReleaseError(f"bundle member is not a portable relative path: {name!r}")
    for segment in name.split("/"):
        core = segment.rstrip(". ")
        if core == "":
            raise ReleaseError(f"bundle member segment has no usable name: {name!r}")
        if core != segment:
            raise ReleaseError(f"bundle member uses a Windows alias suffix: {name!r}")
        if any(ord(char) < 32 or char in '<>"|?*' for char in core):
            raise ReleaseError(f"bundle member uses a non-portable Windows character: {name!r}")
        device = core.split(".", 1)[0].casefold().translate(str.maketrans("¹²³", "123"))
        if device in _WINDOWS_RESERVED:
            raise ReleaseError(f"bundle member uses a reserved Windows device name: {name!r}")


def _check_alias_conflicts(names: list[str]) -> None:
    """Refuse members that collide as case-insensitive Windows path aliases."""

    seen: dict[str, str] = {}
    prefixes: dict[str, str] = {}
    for name in names:
        folded = name.casefold()
        if folded in seen:
            raise ReleaseError(f"bundle members collide as Windows path aliases: {seen[folded]!r} and {name!r}")
        seen[folded] = name
        parts = name.split("/")
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            key = prefix.casefold()
            if key in prefixes and prefixes[key] != prefix:
                raise ReleaseError(f"bundle members collide as Windows path aliases: {prefix!r}")
            prefixes[key] = prefix
    for name in names:
        parts = name.split("/")
        if any("/".join(parts[:index]).casefold() in seen for index in range(1, len(parts))):
            raise ReleaseError(f"bundle member conflicts with a parent file: {name!r}")


@dataclass(frozen=True)
class _BundleMember:
    name: str
    size: int


def _copy_exact(source: BinaryIO, target: BinaryIO, size: int) -> None:
    while size:
        chunk = source.read(min(size, 64 * 1024))
        if not chunk:
            raise ReleaseError("unreadable bundle: truncated tar content")
        target.write(chunk)
        size -= len(chunk)


def _check_pax(data: bytes) -> None:
    offset = 0
    while offset < len(data):
        space = data.find(b" ", offset)
        if space < 0 or space - offset > 10 or not data[offset:space].isdigit():
            raise ReleaseError("unreadable bundle: malformed PAX metadata")
        length = int(data[offset:space])
        end = offset + length
        if end > len(data) or end <= space + 1 or data[end - 1 : end] != b"\n":
            raise ReleaseError("unreadable bundle: malformed PAX metadata")
        key, separator, _value = data[space + 1 : end - 1].partition(b"=")
        # Reject sparse encodings before tarfile can interpret their maps or
        # read additional blocks using metadata-controlled lengths.
        if not separator or key.startswith(b"GNU.sparse") or key == b"size":
            raise ReleaseError("bundle sparse or size-overriding PAX metadata is unsupported")
        offset = end


def _spool_checked_tar(source: BinaryIO, target: BinaryIO) -> None:
    """Bound physical headers and payloads before tarfile interprets metadata."""

    count = physical = total = 0
    pending_metadata: set[bytes] = set()
    while True:
        header = source.read(tarfile.BLOCKSIZE)
        if len(header) != tarfile.BLOCKSIZE:
            raise ReleaseError("unreadable bundle: missing complete tar end markers")
        target.write(header)
        if not any(header):
            second = source.read(tarfile.BLOCKSIZE)
            if len(second) != tarfile.BLOCKSIZE or any(second):
                raise ReleaseError("unreadable bundle: incomplete tar end markers")
            target.write(second)
            trailer = 2 * tarfile.BLOCKSIZE
            if trailer > BUNDLE_MAX_TRAILER_BYTES:
                raise ReleaseError("bundle exceeds the tar padding limit")
            # Reach gzip EOF to verify its checksum/trailer, while refusing
            # hidden members or an unbounded zero-padding compression bomb.
            while chunk := source.read(min(64 * 1024, BUNDLE_MAX_TRAILER_BYTES - trailer + 1)):
                trailer += len(chunk)
                if any(chunk) or trailer > BUNDLE_MAX_TRAILER_BYTES:
                    raise ReleaseError("bundle has trailing data or excessive tar padding")
                target.write(chunk)
            return
        member = tarfile.TarInfo.frombuf(header, "utf-8", "surrogateescape")
        physical += 1
        if physical > BUNDLE_MAX_MEMBERS * 3 + 2:
            raise ReleaseError("bundle declares too many members or metadata headers")
        if member.type in _METADATA_TYPES:
            if member.type in pending_metadata:
                raise ReleaseError("bundle repeats metadata before a regular member")
            pending_metadata.add(member.type)
            if not 0 <= member.size <= BUNDLE_MAX_METADATA_BYTES:
                raise ReleaseError("bundle metadata exceeds the size limit")
            metadata = source.read(member.size)
            if len(metadata) != member.size:
                raise ReleaseError("unreadable bundle: truncated metadata")
            if member.type == tarfile.XHDTYPE:
                _check_pax(metadata)
            target.write(metadata)
            _copy_exact(source, target, -member.size % tarfile.BLOCKSIZE)
            continue
        if member.type not in _REGULAR_TYPES:
            raise ReleaseError(f"bundle member {member.name!r} is not a regular file (type {member.type!r})")
        pending_metadata.clear()
        count += 1
        if count > BUNDLE_MAX_MEMBERS:
            raise ReleaseError("bundle declares too many members")
        if not 0 <= member.size <= BUNDLE_MAX_FILE_BYTES:
            raise ReleaseError(f"bundle member {member.name!r} exceeds the per-file size limit")
        total += member.size
        if total > BUNDLE_MAX_TOTAL_BYTES:
            raise ReleaseError("bundle exceeds the total extracted size limit")
        _copy_exact(source, target, member.size + (-member.size % tarfile.BLOCKSIZE))


@contextmanager
def _read_headers(bundle_path: Path, staging: Path) -> Iterator[tuple[list[_BundleMember], tarfile.TarFile]]:
    """Validate the complete gzip and bounded tar before exposing any content."""

    try:
        with tempfile.TemporaryFile(dir=staging) as spool, gzip.open(bundle_path, "rb") as compressed:
            _spool_checked_tar(compressed, spool)
            spool.seek(0)
            with tarfile.open(fileobj=spool, mode="r:") as tar:
                members = tar.getmembers()
                parsed: list[_BundleMember] = []
                for member in members:
                    if member.type not in _REGULAR_TYPES or member.sparse is not None:
                        raise ReleaseError(f"bundle member {member.name!r} is not a regular file")
                    _validate_member_name(member.name)
                    parsed.append(_BundleMember(name=member.name, size=member.size))
                yield parsed, tar
    except (tarfile.TarError, EOFError, OSError, zlib.error, RecursionError) as exc:
        raise ReleaseError(f"unreadable bundle: {exc}") from exc


def _extract_member(tar: tarfile.TarFile, name: str, target: Path) -> None:
    member = tar.getmember(name)
    handle = tar.extractfile(member)
    if handle is None:
        raise ReleaseError(f"bundle member {name!r} is not readable content")
    target.parent.mkdir(parents=True, exist_ok=True)
    with handle, target.open("xb") as output:
        _copy_exact(handle, output, member.size)


def extract_bundle(bundle_path: Path, staging: Path) -> dict:
    """Boundedly extract one bundle into an isolated staging directory.

    Header checks (count, types, names, sizes, alias conflicts) run before
    any extraction; the manifest member is validated first and every other
    member must be declared by it. Returns the raw manifest mapping; the
    caller runs the standard digest/admission/compatibility gates on the
    extracted bytes.
    """

    if not bundle_path.is_file():
        raise ReleaseError(f"bundle not found: {bundle_path}")
    if bundle_path.stat().st_size > BUNDLE_MAX_TOTAL_BYTES:
        raise ReleaseError("bundle file itself exceeds the total size limit")
    with _read_headers(bundle_path, staging) as (members, tar):
        names = [member.name for member in members]
        _check_alias_conflicts(names)
        if MANIFEST_MEMBER not in names:
            raise ReleaseError(f"bundle carries no {MANIFEST_MEMBER}")
        declared_sizes = {member.name: member.size for member in members}
        if declared_sizes[MANIFEST_MEMBER] > min(BUNDLE_MAX_FILE_BYTES, BUNDLE_MAX_JSON_BYTES):
            raise ReleaseError("bundle manifest exceeds the size limit")

        _extract_member(tar, MANIFEST_MEMBER, staging / MANIFEST_MEMBER)
        try:
            raw = read_artifact_json(staging / MANIFEST_MEMBER, "bundle agent manifest")
        except ArtifactAdmissionError as exc:
            raise ReleaseError(f"unreadable bundle agent manifest: {exc}") from exc
        try:
            manifest = validate_manifest_dict(raw)
        except ManifestError as exc:
            raise ReleaseError(f"bundle agent manifest is invalid: {exc}") from exc
        signature_ref = manifest.signature_ref
        if not isinstance(signature_ref, str) or not signature_ref:
            raise ReleaseError("bundle manifest declares no signature_ref; only signed bundles install")
        whitelist = {MANIFEST_MEMBER, signature_ref, *(f"files/{entry.path}" for entry in manifest.files)}
        _check_alias_conflicts(sorted(whitelist))
        missing = [name for name in whitelist if name not in names]
        if missing:
            raise ReleaseError(f"bundle is missing declared members: {sorted(missing)}")
        unknown = [name for name in names if name not in whitelist]
        if unknown:
            raise ReleaseError(f"bundle carries undeclared members: {sorted(unknown)}")
        if declared_sizes[signature_ref] > BUNDLE_MAX_JSON_BYTES:
            raise ReleaseError("bundle signature envelope exceeds the size limit")

        for name in names:
            if name == MANIFEST_MEMBER:
                continue
            _extract_member(tar, name, _plain_child(staging, name))
        return raw


def pack_bundle(artifact_dir: Path, out_path: Path, policy: ArtifactTrustPolicy) -> str:
    """Verify one artifact and write its deterministic bundle archive.

    Returns the release id (manifest sha256). The output is published
    atomically after a full round-trip verification, so a failure never
    leaves a seemingly complete bundle behind, and an existing file is
    never overwritten.
    """

    if out_path.exists() or out_path.is_symlink():
        raise ReleaseError(f"bundle output already exists: {out_path}")
    if artifact_dir.is_symlink() or artifact_dir.is_junction():
        raise ReleaseError("artifact source must not be a symlink or junction")
    source = artifact_dir.resolve()
    out_resolved = out_path.resolve()
    if out_resolved == source or out_resolved.is_relative_to(source):
        raise ReleaseError("bundle output must not live inside the artifact directory")

    workdir = Path(tempfile.mkdtemp(prefix="hecate-pack-"))
    try:
        staging = workdir / "staging"
        staging.mkdir()
        member_names = _stage_pack_source(source, staging)
        raw = _verify_staged_content(staging)
        try:
            verify_artifact_admission(raw, staging, policy)
        except ArtifactAdmissionError as exc:
            raise ReleaseError(f"artifact admission refused: {exc}") from exc
        verify_manifest_compat(validate_manifest_dict(raw))
        manifest_sha256 = sha256_hex(canonical_manifest_bytes(raw))

        staged_out = out_resolved.parent / f"{out_path.name}.{uuid.uuid4().hex}.tmp"
        staged_out.parent.mkdir(parents=True, exist_ok=True)
        try:
            with staged_out.open("xb") as raw_stream:
                with (
                    gzip.GzipFile(filename="", mode="wb", fileobj=raw_stream, compresslevel=9, mtime=0) as gz,
                    tarfile.open(fileobj=gz, mode="w|", format=tarfile.GNU_FORMAT) as tar,
                ):
                    for name in member_names:
                        path = _plain_child(staging, name)
                        info = tarfile.TarInfo(name=name)
                        info.size = path.stat().st_size
                        info.mtime = 0
                        info.mode = 0o644
                        info.type = tarfile.REGTYPE
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        with path.open("rb") as content:
                            tar.addfile(info, content)
                raw_stream.flush()
                os.fsync(raw_stream.fileno())
            # Round-trip the staged bundle before publishing: only a bundle
            # that installs is allowed to look complete.
            roundtrip = workdir / "roundtrip"
            roundtrip.mkdir()
            extracted_raw = extract_bundle(staged_out, roundtrip)
            if sha256_hex(canonical_manifest_bytes(extracted_raw)) != manifest_sha256:
                raise ReleaseError("packed bundle does not round-trip to the same artifact")
            # A same-directory hard link publishes complete bytes atomically
            # and fails if another writer won; replace would destroy its file.
            try:
                os.link(staged_out, out_resolved)
            except FileExistsError as exc:
                raise ReleaseError(f"bundle output already exists: {out_path}") from exc
        finally:
            if staged_out.exists():
                staged_out.unlink()
        return manifest_sha256
    except (OSError, ArtifactAdmissionError, ManifestError, tarfile.TarError, RecursionError) as exc:
        raise ReleaseError(f"cannot pack artifact: {exc}") from exc
    finally:
        checked = _plain_child(workdir.parent, workdir.name)
        shutil.rmtree(checked, ignore_errors=True)


def _stage_pack_source(source: Path, staging: Path) -> list[str]:
    """Bound source reads and copies before allocating an archive or parsing JSON."""

    manifest_path = _plain_child(source, MANIFEST_MEMBER)
    manifest_limit = min(BUNDLE_MAX_FILE_BYTES, BUNDLE_MAX_JSON_BYTES)
    with manifest_path.open("rb") as stream:
        manifest_bytes = stream.read(manifest_limit + 1)
    if len(manifest_bytes) > manifest_limit:
        raise ReleaseError("bundle manifest exceeds the per-file size limit")
    (staging / MANIFEST_MEMBER).write_bytes(manifest_bytes)
    raw = read_artifact_json(staging / MANIFEST_MEMBER, "agent manifest")
    manifest = validate_manifest_dict(raw)
    if not manifest.signature_ref:
        raise ReleaseError("artifact declares no signature_ref; only signed bundles pack")
    names = sorted([MANIFEST_MEMBER, manifest.signature_ref, *(f"files/{entry.path}" for entry in manifest.files)])
    if len(names) > BUNDLE_MAX_MEMBERS:
        raise ReleaseError("bundle declares too many members")
    for name in names:
        _validate_member_name(name)
    _check_alias_conflicts(names)
    total = 0
    for name in names:
        path = _plain_child(source, name)
        if not path.is_file():
            raise ReleaseError(f"artifact admission refused: missing plain file {name!r}")
        size = path.stat().st_size
        limit = (
            min(BUNDLE_MAX_FILE_BYTES, BUNDLE_MAX_JSON_BYTES)
            if name == manifest.signature_ref
            else BUNDLE_MAX_FILE_BYTES
        )
        if size > limit:
            raise ReleaseError(f"bundle member {name!r} exceeds the per-file size limit")
        total += size
        if total > BUNDLE_MAX_TOTAL_BYTES:
            raise ReleaseError("bundle exceeds the total extracted size limit")
        if name == MANIFEST_MEMBER:
            continue
        target = _plain_child(staging, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with path.open("rb") as content, target.open("xb") as output:
            _copy_exact(content, output, size)
            if content.read(1):
                raise ReleaseError(f"artifact changed while packing: {name!r}")
    return names
