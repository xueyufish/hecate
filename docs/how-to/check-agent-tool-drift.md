# Check agent tool contract drift

An agent version freezes which tools it uses and, for new versions, captures their model-visible `name`, `description` and `parameters` as diagnostic evidence. The live definitions remain independently mutable. The drift check on the version panel therefore reports two separate layers: the recorded reference names (skills and tool names, unchanged semantics) and, per tool, a contract diagnostic comparing what the version committed against what the same tool name resolves to right now.

## What the snapshot records

Committing a version resolves every configured tool name exactly the way execution assembly does — builtin definitions win, then the agent workspace's non-deleted tool rows, through the same loading and formatting pipeline. For each name the snapshot records the model-visible contract, a digest over that contract, the digest scope and format version, and a verifiable resolution source: the builtin identity, or the database row's id, update time and declared source. No external MCP/REST call is made and no credential, implementation code or connector configuration is copied — a registered MCP or REST tool is snapshotted as its stored definition. A name that resolves to nothing at commit time still commits, recorded explicitly as unresolved; the diagnostic introduces no new commit or publish blocking, and existing publication receipts and content hashes keep their meanings.

## Reading the diagnostic

Each frozen tool name gets exactly one status:

- **一致 (consistent)** — the name resolves now to the same recorded contract.
- **契约变化 (changed)** — it resolves but the contract digest moved; the panel shows the recorded and current digests.
- **工具缺失 (missing)** — the frozen name no longer resolves (removed or soft-deleted).
- **未核验 (unverified)** — there is no usable baseline: the version predates contract capture, the name was already unresolvable at commit, or the saved evidence is missing, malformed, uses an unsupported format/scope or fails its content-digest check. The diagnostic never repairs or backfills those records.

The comparison object is the checked version's frozen tool list. Renaming or removing tools on the live draft never swaps it: the frozen name is re-resolved against the current registry regardless of the draft, while the legacy reference list continues to report the draft change at the name level.

A same-name database rebuild deserves attention: when the live row or declared dispatch source differs from the committed binding, the diagnostic flags the binding change separately from contract content. Equal contract hashes do not prove the original binding survived. Definitions and source identities come from the same resolution lookup, and update-time changes alone are not binding changes.

## What the verdict does not claim

- "已核验工具契约全部一致" appears only when at least one tool is verified and none has changed content, a changed binding, missing content or an unverified baseline. Unsupported diagnostic formats/scopes are also unverified. A version whose snapshots predate the feature reports unverified rather than a fabricated baseline.
- An empty reference-drift list says nothing about tool contracts — the two layers are shown separately.
- The diagnostic is configuration evidence only: it does not prove the runtime implementation, permissions or business behavior are fixed, and it never blocks a publish by itself.
- Skill pinning semantics are untouched: pinned skills still have nothing to drift.

## Verification pointers

Snapshot construction, resolution precedence, workspace scoping, soft delete, same-name rebuild, key-order invariance, draft independence, legacy versions and immutability are covered in `tests/test_api/test_agent_versioning.py`; publication receipts and access boundaries in `tests/test_api/test_agent_publication_receipt.py` and `tests/test_api/test_agent_version_access_boundary.py`; the panel rendering and its late-response isolation in `web/src/__tests__/version-panel.test.tsx`.
