# Install and start signed Runner artifacts

Set up a Runner environment and a profile directory with `runner.json`,
`identity.json`, and any configured secrets. Keep the profile directory,
its evidence, and its durable database at stable locations. Follow the
[signing guide](sign-runner-artifacts.md) to create the artifact and
configure the operator's trust policy.

## Install and inspect artifacts

```bash
hecate-runner-release install --host /path/profile --artifact /path/artifact --trust-policy /path/artifact-trust.json
hecate-runner-release list --host /path/profile
```

The installer verifies the signed artifact and publishes it under
`releases/<manifest-sha256>/` in the profile directory. It also accepts
a signed bundle.

```bash
hecate-runner-release pack --artifact /path/artifact --out release.tar.gz --trust-policy /path/artifact-trust.json
hecate-runner-release install --host /path/profile --bundle release.tar.gz --trust-policy /path/artifact-trust.json
```

A bundle carries the signed artifact and its listed content. Provision
the profile configuration and secrets separately.

## Start an explicit artifact

```bash
hecate-runner --profile /path/profile --artifact /path/profile/releases/MANIFEST_SHA256 --artifact-trust /path/artifact-trust.json
```

Startup verifies the selected artifact under the current trust policy.
Relative secrets and evidence paths resolve from the profile directory.
The runner holds a lock on that profile while serving an explicit
artifact.

To choose another installed artifact, wait for active work to settle,
stop the running process, and start it with the new artifact path and
the same profile and durable database. Existing task history and action
receipts stay in that database.
