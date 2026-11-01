# Sign runner artifacts and admit trusted publishers

The standalone runner starts from a profile directory: an execution
manifest, its listed files, host configuration and identity material. The
loader always verifies each manifest-listed file's digest. That proves the
files match the manifest — it says nothing about who produced them. Artifact
signing closes that gap: an operator signs the manifest offline with an
Ed25519 key, the host is started with an operator-provided trust policy, and
strict admission refuses to start anything the policy does not back.

This guide covers key generation, signing, the trust policy, strict startup,
and key rotation or revocation. It describes startup-time trust only: a
running host does not re-check the policy while serving, and no claim is
made about runtime revocation.

## Generate a signing key

Keep the signing key offline — it never belongs in the artifact, the profile
directory or any log:

```bash
hecate-runner-sign generate --out-private-key acme-signing-key.pem --out-public-key acme-signing-key.pub.pem
```

Both output paths must be new and distinct; generation never overwrites an
existing key. On Windows, restrict private-key access using the operator's
file ACLs; POSIX creation restricts the private file to its owner.

## Declare the publisher and sign

The manifest itself must declare the publisher and name the signature file
before signing — the publisher identity is part of the signed content, never
an envelope-only claim:

```json
{
  "publisher_ref": "publisher:acme",
  "signature_ref": "artifact-signature.json",
  "...": "rest of the manifest"
}
```

Then sign. The signature binds the complete manifest — file digests, tool
permissions, entry and publisher — through its canonical form:

```bash
hecate-runner-sign sign \
  --manifest profile/agent-manifest.json \
  --key acme-signing-key.pem \
  --publisher-ref publisher:acme \
  --key-id acme-current
```

The command writes to the manifest's profile-relative `signature_ref`.
Its parent directory must already exist. An explicit `--out` must resolve
to that same path and cannot replace the manifest, signing key, host config
or a manifest-listed file. A manifest edited after signing
no longer matches the recorded digest and is refused — re-sign after every
manifest change.

## Configure the artifact trust policy

The trust policy is operator-owned host configuration, provided at start —
trust is never established from the artifact's own declarations, an HTTP
request or a public key attached to the artifact:

```json
{
  "artifact_trust_version": "1",
  "publishers": {
    "publisher:acme": {
      "keys": [
        {"key_id": "acme-current", "public_key": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"}
      ],
      "revoked": false,
      "revoked_key_ids": []
    }
  }
}
```

Public keys are inlined in PEM (subjectPublicKeyInfo) form. A publisher with
`"revoked": true`, a key listed in `revoked_key_ids`, or a key removed from
the policy admits nothing. An empty `publishers` object is a valid deny-all
posture. Entries in `revoked_key_ids` must still be present in `keys`; to
remove a key entirely, remove its revocation-list entry as well.

## Start the runner under strict admission

```bash
hecate-runner --profile profile --artifact-trust artifact-trust.json
```

Strict admission refuses to start — before any service bind, model call or
tool execution — when the manifest is unsigned, the envelope is malformed,
the publisher or key is unknown or revoked, the publisher in the envelope
differs from the manifest, the signature algorithm or version is unsupported,
the signature does not verify, or the manifest or any listed file fails its
digest check. There is no fallback to digest-only mode. A preflight check
without starting the host:

```bash
hecate-runner-sign verify --profile-dir profile --trust-policy artifact-trust.json
```

Preflight verifies the publisher signature and the size and digest of every
listed file, without loading host credentials or starting execution resources.
It does not validate host configuration or grant runtime permissions. Manifest,
envelope and trust-policy JSON reject duplicate members and non-finite numbers.

The serving line labels the mode honestly: `artifact-admission=strict
publisher=... key=...` under strict admission, `artifact-admission=digest-only`
without the flag. Digest-only — or a merely present `signature_ref` — is not
publisher authentication.

## Rotate or revoke a key

Rotation: add the new key under a fresh `key_id` to the policy, re-sign the
artifact with the new key, restart the host. Revocation: remove the key from
the policy, list its `key_id` in `revoked_key_ids`, or set the publisher's
`revoked` flag, then restart. An already-running host keeps serving until it
is restarted — restart is the enforcement point, and that limitation is
explicit rather than implied.

## What the signature covers — and what it does not

The signature covers the manifest and, through the manifest's digests, every
listed file: the entry definition and each declared tool schema. It does not
cover `runner.json`, `identity.json` or the trust policy — those remain
operator-managed host configuration whose lifecycle is documented in the
runner README. Admission also composes with, and never replaces, the
existing capability, permission, approval and lease checks: a signature that
verifies grants no additional runtime privilege.
