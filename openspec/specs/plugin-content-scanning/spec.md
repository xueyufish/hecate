## Purpose

Deterministic install/enable-time content scanning for declarative (T4) agent-plugin packages: a rule engine detects prompt-injection patterns, invisible-Unicode smuggling, secret material, and permission-declaration risk in package content before it can reach agent context, with fail-closed verdicts, ops-console findings, and administrator acknowledgment.

## ADDED Requirements

### Requirement: Detection surface
The scanner SHALL analyze package text content for five rule categories: (a) prompt-injection patterns via regex and heuristics (instruction-override phrasing, fake system-prompt or tool-result framing, exfiltration commands); (b) invisible-Unicode steganography; (c) secret material (private keys, API tokens, JWTs, connection strings); (d) `allowed-tools` pre-authorization audit — declared tool grants SHALL be reported as findings weighted by tool risk, not merely validated for well-formedness; (e) high-confidence suspicious URLs (paste-site domains, IP-literal endpoints, homograph-confusable domains). Full declared-versus-extracted domain reconciliation is out of scope for v1.

#### Scenario: Injection phrase in skill body flagged
- **WHEN** a SKILL.md body contains a known instruction-override phrase
- **THEN** the scan produces an injection-category finding identifying the file and rule

#### Scenario: Secret in mcp.json env flagged
- **WHEN** an mcp.json env value matches a private-key or API-token pattern
- **THEN** the scan produces a secret-category finding

#### Scenario: Dangerous allowed-tools declaration reported
- **WHEN** a skill frontmatter declares `allowed-tools` granting shell execution or unrestricted filesystem write
- **THEN** the scan produces an audit finding listing the granted tool surface

#### Scenario: Paste-site URL flagged
- **WHEN** a skill body references a known paste-site domain as an instruction target
- **THEN** the scan produces a suspicious-URL finding

### Requirement: Invisible-Unicode detection
The scanner SHALL detect invisible and directionality-abusing codepoints in scanned text: zero-width characters, bidi override characters, variation selectors, ANSI escape sequences, and Unicode tag-block characters. Thresholds SHALL apply: a contiguous tag-block character run exceeding 10 codepoints SHALL be high severity, and a total of suspicious codepoints in one file exceeding 100 SHALL itself be flagged.

#### Scenario: Tag-character smuggling run flagged high
- **WHEN** a file contains a run of 12 tag-block codepoints encoding hidden ASCII text
- **THEN** the scan emits a high-severity invisible-Unicode finding

#### Scenario: Incidental zero-width characters below threshold
- **WHEN** a file contains two isolated zero-width spaces with no other suspicious codepoints
- **THEN** the scan emits no invisible-Unicode finding

### Requirement: Obfuscation decode layer
The scanner SHALL apply deterministic transform passes before pattern matching: Unicode compatibility normalization (NFKC) on scanned text, and bounded base64/hex decoding of candidate encoded blobs that pass strict pre-checks (minimum length, valid charset, decoding to mostly-printable text), re-running the high-severity rule set on decoded content. Every finding produced via a transform SHALL record which transform exposed it. Full confusables-table homoglyph mapping and entity/escape decoding are deferred beyond v1.

#### Scenario: Base64-encoded exfiltration command detected
- **WHEN** a file contains a base64 blob that decodes to a known exfiltration command
- **THEN** the scan produces a finding whose recorded transform is base64

#### Scenario: Legitimate binary blob not decoded into findings
- **WHEN** a file contains a base64 blob that decodes to non-printable binary content
- **THEN** the decode pass produces no finding for that blob

### Requirement: File-role severity matrix
Finding severity SHALL be assigned from rule-intrinsic severity combined with file role, where role reflects runtime exposure: skill frontmatter description and skill body (both injected into agent context by skill loading) and mcp.json credential values are highest-exposure roles; nested supporting files readable by agents on demand are medium; README and catalog-facing text are low. The severity matrix SHALL be fixed platform behavior, not per-package or per-workspace configuration.

#### Scenario: Same phrase tiered by location
- **WHEN** the same medium-intrinsic injection phrase appears in a skill frontmatter description and in a README
- **THEN** the description occurrence receives higher severity than the README occurrence

#### Scenario: Frontmatter smuggling treated as highest exposure
- **WHEN** an invisible-Unicode smuggling run appears in a skill description field
- **THEN** the finding receives the highest severity the rule set assigns

### Requirement: Verdict computation
Each scan SHALL produce exactly one verdict — allow, warn, or block — computed as the highest finding severity evaluated against a configurable blocking threshold: findings at or above the threshold yield block, remaining medium-or-higher findings yield warn, otherwise allow. The threshold SHALL be platform configuration defaulting to `high`.

#### Scenario: Default threshold blocks on high
- **WHEN** a scan finds one high-severity finding with the default threshold
- **THEN** the verdict is block

#### Scenario: Strict threshold blocks on medium
- **WHEN** the threshold is set to medium and a scan finds one medium-severity finding
- **THEN** the verdict is block

#### Scenario: Clean package allowed
- **WHEN** a scan finds no findings
- **THEN** the verdict is allow

### Requirement: Fail-closed install enforcement
Installation SHALL be rejected when the scan verdict is block, with findings included in the rejection response. A scanner failure (unhandled error, unreadable file) SHALL reject the install rather than allow it. Text files exceeding the per-file scan size cap (default 1 MB) SHALL produce an oversize finding whose severity follows the file-role matrix — content SHALL never be silently skipped for size. Binary files SHALL be skipped by content type without producing findings.

#### Scenario: Block verdict rejects install
- **WHEN** an install scan yields verdict block
- **THEN** no package row or directory is persisted and the error carries the findings

#### Scenario: Scanner crash rejects install
- **WHEN** the scanner raises an unexpected error during an install scan
- **THEN** the install is rejected with a scanner-failure error

#### Scenario: Oversized text file flagged rather than skipped
- **WHEN** a nested supporting text file exceeds the scan size cap
- **THEN** the scan emits an oversize finding and the file's content is not scanned

#### Scenario: Binary asset skipped silently
- **WHEN** a package contains image or font files
- **THEN** the scanner skips them without producing findings

### Requirement: Finding schema and evidence redaction
Each finding SHALL carry a rule identifier, category, severity, package-relative file path, approximate location, the transform that exposed it (none, normalization, base64, or hex), and a truncated evidence fingerprint of at most 8 characters plus its length. Findings SHALL NOT persist full secret material or complete malicious payloads.

#### Scenario: Secret evidence truncated
- **WHEN** a finding exposes an API key
- **THEN** the persisted evidence contains at most the key's first 8 characters and its length

#### Scenario: Transform recorded for decoded finding
- **WHEN** a finding is detected in NFKC-normalized text
- **THEN** the finding records the normalization transform

### Requirement: Enable-time rescan
Enabling a package SHALL re-run the scan when the stored scan result is absent or was produced by a different scanner version. A rescan verdict of block SHALL refuse the enable with findings returned. Packages installed before scanning shipped (null scan result) SHALL be scanned on their first enable after upgrade.

#### Scenario: Scanner-version drift triggers rescan
- **WHEN** a package's stored result carries scanner v1 and the platform runs v2
- **THEN** enabling re-scans and stores results under v2

#### Scenario: Block on rescan refuses enable
- **WHEN** an enable-time rescan yields block
- **THEN** the package remains not-enabled and findings are returned

#### Scenario: Legacy package backfilled on first enable
- **WHEN** a package installed during the no-op era with null scan result is enabled
- **THEN** it is scanned and the result persisted

### Requirement: Ops Center projection
Scan findings SHALL be projected as security findings for the ops console, deduplicated by (content hash, scanner version): rescans with an unchanged dedup key SHALL NOT create duplicate finding rows. Blocked install attempts SHALL also project a finding recording the attempt, idempotent per (package name, origin, rule identifier).

#### Scenario: Unchanged rescan creates no duplicate rows
- **WHEN** a package is disabled and re-enabled with unchanged content and scanner version
- **THEN** no new security-finding rows are created for already-projected rules

#### Scenario: Blocked attempt recorded
- **WHEN** an install is rejected by a block verdict
- **THEN** a security finding records the attempt marked as an install-blocked phase

### Requirement: Acknowledgment suppression
Warn-or-lower findings acknowledged by an administrator SHALL be suppressed in later rescans of identical content: acknowledgments key on (content hash, rule identifier), and any content change SHALL invalidate prior acknowledgments. Acknowledgment SHALL NOT suppress findings at or above the blocking threshold.

#### Scenario: Acknowledged warn not re-raised
- **WHEN** a warn finding is acknowledged and identical content is rescanned
- **THEN** that finding is suppressed and does not affect the verdict

#### Scenario: Content change invalidates acknowledgments
- **WHEN** a package is updated after an acknowledgment
- **THEN** prior acknowledgments no longer suppress findings

#### Scenario: High-severity finding never suppressed
- **WHEN** content carries both an acknowledged warn and a new high-severity finding
- **THEN** the high-severity finding still yields block

### Requirement: Scan result API
The platform SHALL expose `GET /api/plugins/{id}/scan` returning the package's current scan state: verdict, scanner version, findings, and scan timestamp. For plugins whose type is not an agent-plugin package, the endpoint SHALL report that scanning is not applicable.

#### Scenario: Scan state retrieved
- **WHEN** the endpoint is called for a scanned agent-plugin package
- **THEN** the response includes verdict, findings, and scanner version

#### Scenario: Non-agent-plugin not applicable
- **WHEN** the endpoint is called for a plugin.yaml-based plugin
- **THEN** the response indicates scanning is not applicable

### Requirement: Configuration and go-live
Scanning SHALL be governed by platform configuration: the blocking severity threshold (default high), the per-file scan size cap (default 1 MB), and the ingestion master switch, which SHALL default to enabled once this capability ships while retaining its emergency kill-switch role. All scanning knobs SHALL default to fail-closed values.

#### Scenario: Master switch defaults on after go-live
- **WHEN** the platform starts with default configuration after this change ships
- **THEN** agent-plugin ingestion is enabled

#### Scenario: Kill switch still disables ingestion
- **WHEN** the operator sets the ingestion master switch off
- **THEN** installs are refused with a feature-disabled error
