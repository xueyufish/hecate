## Purpose

Behavior contract for intent packages as governed workspace assets: a named set of intent categories with sample utterances, bulk CSV/JSON import/export, named immutable versions with a deterministic publish gate, correction backflow with provenance, and linkage to recognition-accuracy evaluation.

## ADDED Requirements

### Requirement: Intent package CRUD with workspace isolation

The system SHALL support creating, listing, reading, updating, and deleting intent packages scoped to a workspace. A package SHALL contain a name (unique within its workspace) and a set of categories, each with a name, a description used as classification guidance, and a list of sample utterances. Access SHALL be denied across workspace boundaries.

#### Scenario: Create and read a package

- **WHEN** a package is created with name "billing-intents" and two categories, each carrying sample utterances
- **THEN** the package SHALL be readable with its categories and samples intact, and appear in the workspace package list

#### Scenario: Duplicate name rejected within workspace

- **WHEN** a second package is created with a name already used in the same workspace
- **THEN** the API SHALL respond with a conflict error and no package SHALL be created

#### Scenario: Cross-workspace access denied

- **WHEN** a user attempts to read or modify a package in a workspace they do not belong to
- **THEN** the API SHALL deny access and return no package data

### Requirement: Bulk import and export

The system SHALL support importing categories and sample utterances in bulk from CSV and JSON payloads, and exporting a package as CSV or JSON. Import SHALL be all-or-nothing per invocation: any invalid row SHALL abort the import and return a row-level error report; a successful import SHALL replace or merge category/sample content according to the requested mode.

#### Scenario: Valid JSON import creates categories and samples

- **WHEN** a JSON payload describing three categories with sample utterances is imported in merge mode
- **THEN** the package SHALL contain those categories and samples alongside previously existing ones

#### Scenario: Invalid rows abort the whole import

- **WHEN** an import payload contains one row with an empty category name among otherwise valid rows
- **THEN** the import SHALL abort entirely, no partial content SHALL be written, and the response SHALL report the failing row numbers and reasons

#### Scenario: Export round-trips

- **WHEN** a package is exported as CSV and the file is imported into another package
- **THEN** the target package SHALL contain the same categories, descriptions, and sample utterances

### Requirement: Named immutable versions

The system SHALL support freezing a package's current draft content into a named version containing the frozen categories and samples, a content hash computed with the same canonical serialization used by evaluation dataset snapshots, and the creating user. Version names SHALL be unique per package (including soft-deleted ones). A version's frozen content, name, and content hash SHALL NOT be modifiable after creation. Versions SHALL support list, read, and soft delete; existing runtime references to a soft-deleted version SHALL remain resolvable for reading.

#### Scenario: Freeze captures draft content

- **WHEN** a package has three draft categories and a version "v1" is created, then a sample is added to the draft
- **THEN** version "v1" SHALL contain exactly the three categories and samples frozen at creation, the draft SHALL reflect the added sample, and the version content hash SHALL remain stable

#### Scenario: Duplicate version name rejected

- **WHEN** a version name already exists for the package (even if soft-deleted)
- **THEN** creation SHALL respond with a conflict error

#### Scenario: Soft delete does not break references

- **WHEN** a version that is referenced by running graphs or cached decisions is soft-deleted
- **THEN** it SHALL disappear from default listings, its name SHALL stay reserved, and existing references SHALL remain readable

### Requirement: Deterministic publish gate

Publishing a version SHALL evaluate a gate whose signals are deterministic only: recognition-accuracy pass rate from a linked evaluation run and per-category minimum sample coverage. The gate SHALL support `warn` and `require` modes. In `require` mode, any unmet enabled signal SHALL block publishing with a conflict response carrying the full gate report; the response SHALL support an explicit force flag that publishes anyway, records the bypass in the gate report, and writes an audit entry. LLM-judge or human-only scores SHALL NOT participate in gate blocking. In `warn` mode or with the gate off, publishing SHALL proceed regardless of signals.

#### Scenario: Require mode blocks on low accuracy

- **WHEN** the gate is `require` with `min_pass_rate` 0.8 and the linked recognition run reports a pass rate of 0.6
- **THEN** publishing SHALL be blocked with a conflict response containing per-signal verdicts, and the version SHALL remain unpublished

#### Scenario: Force bypass is audited

- **WHEN** a blocked publish is retried with the force flag
- **THEN** the version SHALL be published, the gate report SHALL record `bypassed_by_force`, and an audit entry SHALL be written

#### Scenario: Coverage signal from category samples

- **WHEN** the gate requires a minimum of 5 samples per category and one frozen category has 2
- **THEN** publishing in `require` mode SHALL be blocked with the coverage shortfall named in the report

#### Scenario: Human scores never block

- **WHEN** the linked run's only failing signal is an LLM-judge or human score below threshold
- **THEN** the gate SHALL NOT block publishing in any mode

### Requirement: Correction backflow with provenance

The system SHALL accept correction submissions that append a sample utterance (or a category correction) to a package's draft with provenance: the source session and turn reference, a correction reason, and the submitter. Corrections SHALL NOT modify any published version. Corrected drafts reach runtime only through the version freeze and publish flow.

#### Scenario: Correction lands in draft only

- **WHEN** a correction appends a misrouted utterance with its true category to a package whose version "v3" is published
- **THEN** the draft SHALL contain the new sample with provenance, version "v3" SHALL be unchanged, and runtime recognition SHALL continue using "v3" evidence until a new version is published

#### Scenario: Provenance traceable

- **WHEN** a correction sample is listed in the draft
- **THEN** its source session/turn reference, reason, and submitter SHALL be readable

### Requirement: Recognition-accuracy evaluation linkage

A package version SHALL support generating an evaluation dataset from its frozen samples (with a configurable held-out split) and recording recognition-accuracy results against that dataset. The publish gate's accuracy signal SHALL be evaluated from the recorded results of the linked run when configured.

#### Scenario: Gate accuracy signal reads the linked run

- **WHEN** a version has a linked recognition-accuracy result and the gate is `require` with an accuracy signal
- **THEN** the gate verdict SHALL be computed from that recorded result and the report SHALL cite the run

#### Scenario: Accuracy report identifies per-category weaknesses

- **WHEN** a recognition-accuracy run completes for a package version
- **THEN** the recorded results SHALL include per-category accuracy so low-coverage or confusable categories are identifiable
