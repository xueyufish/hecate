# Install, switch and roll back runner releases

The signing guide ([Sign runner artifacts](sign-runner-artifacts.md)) makes
one artifact verifiable. This guide turns that into a lifecycle: install
signed artifacts into a release-managed host, choose which one the next
cold start runs, and roll back — without ever touching the host's own
configuration or run data.

The first version covers one host switching while stopped. There is no hot
upgrade, no live-run migration across versions, no remote activation, and
no shared-database multi-host switching; those combinations are refused or
documented as unsupported rather than approximated.

## Host layout

A release-managed host separates operator state from artifact content:

```
<host>/
  runner.json             # operator-owned host config (stable across releases)
  identity.json           # operator-owned trust material
  secrets/, evidence/     # operator-owned; secrets resolve against the host dir
  releases/<sha256>/      # installed artifact content, addressed by manifest digest
  staging/<id>/           # in-progress installs; never selectable
  active.json             # the committed selection, replaced atomically
  host.lock               # OS-level mutex: startup and switching share it
```

A host is any directory carrying `runner.json` and `identity.json`; the
management commands refuse to operate on anything else. Artifact content
never carries host configuration, and the runner resolves secrets, evidence
and identity from the host directory in every mode.

## Install a signed artifact

```bash
hecate-runner-release install --host <host> --artifact <artifact-dir> --trust-policy artifact-trust.json
```

The installer copies only whitelisted content (the manifest, its signature
envelope and the listed files) into staging, refuses symlinked sources,
re-verifies every digest on the copied bytes, runs strict admission under
the current policy, checks backend compatibility and required capabilities,
and only then publishes the release directory under its manifest digest.
A crash leaves at worst an unusable staging directory — never a selectable
release. Re-installing the identical artifact is idempotent; content that
claims an existing release id but differs is refused.

## List, activate, roll back

```bash
hecate-runner-release list --host <host>          # or: status
hecate-runner-release activate --host <host> --release <manifest-sha256> --expected-generation <status-generation> --trust-policy artifact-trust.json
hecate-runner-release rollback --host <host> --expected-generation <status-generation> --trust-policy artifact-trust.json
```

`activate` selects an installed release for the next cold start; `rollback`
re-selects the previously active release (the pointer records it). Both
run under the host lock and therefore refuse while the runner is serving.
Both re-verify the candidate under the CURRENT trust policy and run the
full profile gate against the host configuration — a release that passed
admission when it was installed can still be refused now.

Read `generation` from `status` before choosing a version; before the first
activation it is zero. Each changed selection increments it, including a
rollback. A stale generation is refused even if the current digest has since
returned to the earlier version. Refresh status before deciding again.

Both also refuse to switch while the host's durable ledger still carries
non-terminal work (queued, running, waiting for input, approval or a timer,
or parked for reconciliation), and they refuse when the ledger state cannot
be verified — the ledger is never cleared to let a switch through. Run the
management commands in the host's service environment (same environment
variables), because activation performs the full profile check.

Release-managed durable hosts require an absolute local SQLite database URL
and an existing initialized ledger. The state check opens that file read-only;
it never creates a missing database or schema. The first activation records
the data location, and later starts and switches reject removing or changing
that binding. Missing or unknown binding metadata fails closed. The legacy
`--profile` mode retains its existing database configuration behavior.

The pointer commit is atomic: a failed activation keeps the previous
selection, and after a failed startup the operator can still select an
older installed release offline. Rollback re-selects an artifact only —
tasks, action receipts, approval consumption, budgets, evidence and known
revocations are never rewound, and no database downgrade runs.

## Start the runner from the selection

```bash
hecate-runner --host <host> --artifact-trust artifact-trust.json
```

The startup entry reads the committed selection, re-verifies it under the
current policy, acquires the host lock for its whole lifetime (making a
concurrent switch impossible), and serves the selected release against the
stable host configuration. `--host` and `--profile` are mutually exclusive;
the serving line names the running release digest so what is executing is
never ambiguous.

## Verified scope and unsupported layouts

Verified scope: single-host install, cold-start switching and rollback, with
the host's own local SQLite ledger and evidence state staying in place. Unsupported
in this version: hot upgrade, migrating active runs across versions,
remote activation, remote databases and multiple hosts sharing one durable
database. The host mutex coordinates one installation; it does not discover
other installations or external clients writing the same database. Shared
database layouts are outside this support scope. Static profile checks are
not runtime health or production certification. Startup-time trust only: a
running host does not re-check the trust policy until restart.
