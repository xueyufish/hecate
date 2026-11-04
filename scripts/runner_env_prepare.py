#!/usr/bin/env python3
"""Prepare an offline Runner environment delivery (build machine, online).

Produces a self-contained delivery directory:

    <target>/
      wheelhouse/           # workspace wheels + third-party wheel closure
      requirements.lock     # hash-pinned third-party pins from uv.lock
      environment.json      # verifiable manifest + manifest_sha256

The manifest records the Python/platform target, build-tool versions, the
source revision and lock digest, every wheel file's digest, and packages
whose wheel metadata declares no license. ``runner_env_install.py`` on the
target machine refuses anything this manifest does not back; the operator
moves ``environment.json`` (or just its digest) over an independent
channel, and the installer cross-checks the delivery against it.

Preparation may use the network (wheel download). Installation never does.
The first version certifies same-platform delivery: the manifest pins the
preparing interpreter's Python minor version and platform, and the
installer refuses a mismatched target.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path

MANIFEST_FORMAT_VERSION = 1
WORKSPACE_PACKAGES = ("hecate-runtime", "hecate-governance", "hecate-durable", "hecate-runner")
_WHEEL_FILENAME = re.compile(r"^(?P<name>[^-]+)-(?P<version>[^-]+)(?:-[0-9][A-Za-z0-9_]*)?-[^-]+-[^-]+-[^-]+\.whl$")


class PrepareError(RuntimeError):
    """Environment preparation refused or failed; no delivery was finished."""


def canonical_json(document: object) -> bytes:
    """The deterministic byte form the manifest digest is computed over."""

    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def manifest_digest(manifest: dict) -> str:
    payload = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], *, cwd: Path | None = None) -> str:
    # Fixed, operator-invoked tooling; the command list is never request data.
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)  # noqa: S603
    if result.returncode != 0:
        raise PrepareError(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stderr[-2000:]}")
    return result.stdout


def _tool_version(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    return (result.stdout or result.stderr).strip().splitlines()[0] if result.returncode == 0 else "unavailable"


def _wheel_license(wheel_path: Path) -> str | None:
    """The wheel metadata's license claim, or None when it declares none."""

    with zipfile.ZipFile(wheel_path) as archive:
        metadata_name = next(
            (name for name in archive.namelist() if name.endswith(".dist-info/METADATA")),
            None,
        )
        if metadata_name is None:
            return None
        metadata = BytesParser(policy=policy.default).parsebytes(archive.read(metadata_name), headersonly=True)
        for field in ("License-Expression", "License"):
            claim = str(metadata.get(field, "")).strip()
            if claim and claim.upper() != "UNKNOWN":
                return claim
    return None


def _source_revision(repo_root: Path) -> tuple[str | None, bool | None]:
    try:
        top_level = Path(_run(["git", "rev-parse", "--show-toplevel"], cwd=repo_root).strip()).resolve()
        if top_level == repo_root:
            commit = _run(["git", "rev-parse", "HEAD"], cwd=repo_root).strip()
            dirty = bool(_run(["git", "status", "--porcelain"], cwd=repo_root))
            return commit, dirty
    except (PrepareError, OSError):
        pass  # A source archive may have no Git metadata or executable.
    return None, None


def prepare_environment(target: Path, *, repo_root: Path) -> str:
    """Build a new delivery directory and return its manifest digest."""
    target, repo_root = target.absolute(), repo_root.resolve()
    lock = repo_root / "uv.lock"
    if not lock.is_file():
        raise PrepareError(f"not a workspace root (missing uv.lock): {repo_root}")
    git_commit, git_dirty = _source_revision(repo_root)
    lock_digest = sha256_file(lock)
    try:
        target.mkdir(parents=True)
    except FileExistsError as exc:
        raise PrepareError(f"delivery target already exists: {target}") from exc

    try:
        wheelhouse = target / "wheelhouse"
        wheelhouse.mkdir()
        for package in WORKSPACE_PACKAGES:
            _run(
                [
                    "uv",
                    "build",
                    "--wheel",
                    "--python",
                    sys.executable,
                    "--package",
                    package,
                    "--out-dir",
                    str(wheelhouse),
                ],
                cwd=repo_root,
            )

        requirements_lock = target / "requirements.lock"
        requirements_lock.write_text(
            _run(
                [
                    "uv",
                    "export",
                    "--locked",
                    "--package",
                    "hecate-runner",
                    "--no-dev",
                    "--no-emit-workspace",
                ],
                cwd=repo_root,
            ),
            encoding="utf-8",
        )
        # Download the third-party wheel closure for THIS platform; the
        # manifest pins the platform so the installer can refuse a foreign
        # target instead of failing deep inside pip. The dev interpreter may
        # be a uv venv without pip, so the downloader runs in a throwaway
        # stdlib venv whose ensurepip bootstrap always provides one.
        with tempfile.TemporaryDirectory(prefix="runner-env-prepare-") as bootstrap:
            bootstrap_venv = Path(bootstrap) / "downloader"
            _run([sys.executable, "-I", "-m", "venv", str(bootstrap_venv)])
            downloader_python = bootstrap_venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            pip = [str(downloader_python), "-I", "-m", "pip", "--disable-pip-version-check"]
            pip_version = _tool_version([*pip, "--version"])
            _run(
                [
                    *pip,
                    "download",
                    "--require-hashes",
                    "--no-deps",
                    "--only-binary=:all:",
                    "-r",
                    str(requirements_lock),
                    "-d",
                    str(wheelhouse),
                ],
                cwd=repo_root,
            )

        wheels: list[dict] = []
        license_missing: list[str] = []
        for wheel_path in sorted(wheelhouse.glob("*.whl")):
            match = _WHEEL_FILENAME.match(wheel_path.name)
            if match is None:
                raise PrepareError(f"unrecognized wheel filename: {wheel_path.name}")
            license_claim = _wheel_license(wheel_path)
            if license_claim is None:
                license_missing.append(match.group("name"))
            wheels.append(
                {
                    "filename": wheel_path.name,
                    "sha256": sha256_file(wheel_path),
                    "name": match.group("name").lower(),
                    "version": match.group("version"),
                    "license": license_claim,
                }
            )
        missing = {name.replace("-", "_") for name in WORKSPACE_PACKAGES} - {entry["name"] for entry in wheels}
        if missing:
            raise PrepareError(f"wheelhouse is missing workspace wheels: {sorted(missing)!r}")
        # uv adds a .gitignore to its output directory.
        (wheelhouse / ".gitignore").unlink(missing_ok=True)
        if any(path.suffix != ".whl" for path in wheelhouse.iterdir()):
            raise PrepareError("wheelhouse contains an unexpected non-wheel file")
        if sha256_file(lock) != lock_digest:
            raise PrepareError("uv.lock changed during preparation")

        manifest = {
            "format_version": MANIFEST_FORMAT_VERSION,
            "python_target": f"{sys.version_info.major}.{sys.version_info.minor}",
            "python_implementation": sys.implementation.name,
            "python_abi": sysconfig.get_config_var("SOABI"),
            "platform": f"{sys.platform}:{platform.machine()}",
            "build_tools": {
                "python": platform.python_version(),
                "uv": _tool_version(["uv", "--version"]),
                "pip": pip_version,
            },
            "source": {
                "git_commit": git_commit,
                "git_dirty": git_dirty,
                "uv_lock_sha256": lock_digest,
            },
            "requirements_lock_sha256": sha256_file(requirements_lock),
            "wheels": wheels,
            "license_metadata_missing": license_missing,
        }
        digest = manifest_digest(manifest)
        manifest["manifest_sha256"] = digest
        (target / "environment.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return digest
    except (Exception, KeyboardInterrupt):
        shutil.rmtree(target)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="runner-env-prepare",
        description="Prepare an offline Runner environment delivery (online build-machine step)",
    )
    parser.add_argument("--target", required=True, type=Path, help="delivery directory to create (must not exist)")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        digest = prepare_environment(args.target, repo_root=args.repo_root)
    except (PrepareError, OSError, zipfile.BadZipFile) as exc:
        print(f"runner-env-prepare: {exc}", file=sys.stderr)
        return 2
    print(f"runner-env-prepare: delivery ready at {args.target}")
    print(f"runner-env-prepare: manifest_sha256 {digest}")
    print("Move the digest (or environment.json) to the target machine over an independent trusted channel;")
    print("the installer cross-checks the delivery against it. Installation itself never uses the network.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
