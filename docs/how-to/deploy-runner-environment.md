# Deliver the Runner environment offline

Use a build machine to package the Runner workspace wheels and the
third-party wheels pinned by the workspace `uv.lock`. The target machine
installs the verified wheelhouse from local storage. Prepare a separate
delivery for each target Python version, ABI, and platform.

## Prepare on the build machine

```bash
python scripts/runner_env_prepare.py --target /path/offline-delivery
```

The script refuses an existing target, builds the runtime/durable/
governance/runner wheels, exports the hash-pinned lock from `uv.lock`,
downloads the matching wheels for the preparing platform, and writes:

```
<target>/
  wheelhouse/          # only *.whl; exactly what the manifest lists
  requirements.lock    # hash-pinned pins (name==version + sha256)
  environment.json     # manifest + manifest_sha256
```

The manifest records the target interpreter and platform, the actual
downloader and build-tool versions, the Git revision and whether the
source checkout was dirty, the `uv.lock` digest, and each wheel's digest
and license metadata. Prepare release deliveries from a clean checkout.
The target interpreter must support the delivered wheel tags.

## Transfer and verify

Stage the delivery directory on local storage. Obtain
`runner_env_install.py` from a trusted source and receive the expected
`manifest_sha256` over an **independent trusted channel**. Pass that
digest to the installer.

```bash
python -I /path/to/runner_env_install.py \
  --delivery /path/offline-delivery \
  --expected-manifest-sha256 <digest-from-your-channel> \
  --target /path/new-venv
```

The script is standalone (standard library only): the target machine
needs a preinstalled Python 3.12 with venv/`pip` bootstrap capability and
nothing else — no uv, no repository source, no Runner.

The installer checks the expected digest, the Python target, the locked
pins, and the exact set of wheel files. It copies the wheels into a
private temporary directory and verifies the copies. pip receives only
those local files with `--no-index --no-deps --only-binary=:all:
--require-hashes`, with inherited pip settings and its cache disabled.
CI runs the clean install with a separate network namespace.

The installer reserves the requested path and creates the venv there so
activation and console scripts use the final path. Use it after the
installer returns success. The installer removes its new venv when
installation or `pip check` fails. After a hard interruption, discard
the incomplete directory before retrying.

The new environment passes `pip check` and contains the standalone Runner.
Start it with the new interpreter.

```bash
/path/new-venv/bin/python -m hecate_runner --profile <profile> ...
```

## Release scope

- The wheelhouse is frozen at preparation time. Rebuild it from the
  merged source SHA after integrating new work; do not reuse a delivery
  across versions.
- Review `license_metadata_missing` for wheels without a usable
  `License-Expression` or `License` field.
- Trust origins are layered and independent: the operator's channel for
  the manifest digest, the artifact's publisher admission for execution
  content (see [Sign runner artifacts](sign-runner-artifacts.md)), and the
  preinstalled Python runtime.
- Air-gapped deployment also requires provisioning Python, model weights,
  and any optional adapters.
