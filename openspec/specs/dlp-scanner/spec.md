# dlp-scanner Specification

## Purpose
TBD - created by archiving change outbound-dlp-engine. Update Purpose after archive.
## Requirements
### Requirement: DLPAction enum
The system SHALL define `DLPAction` as a `StrEnum` with four members: `ALLOW`, `BLOCK`, `MASK`, `AUDIT`.

#### Scenario: String comparison
- **WHEN** `result.action == DLPAction.ALLOW`
- **THEN** the comparison evaluates to `True`

#### Scenario: Four members
- **WHEN** `len(DLPAction)` is evaluated
- **THEN** the result is `4`

#### Scenario: Most restrictive wins
- **WHEN** multiple findings have different actions `[MASK, BLOCK, AUDIT, ALLOW]`
- **THEN** `overall_action()` SHALL return `BLOCK` (highest severity)

### Requirement: DLPFinding dataclass
The system SHALL define `DLPFinding` as a dataclass with fields: `entity_type` (str), `value` (str), `start` (int), `end` (int), `score` (float, 0.0-1.0), `recognizer` (str).

#### Scenario: Finding with all fields
- **WHEN** `DLPFinding(entity_type="EMAIL", value="john@example.com", start=10, end=26, score=0.95, recognizer="regex_pii")` is constructed
- **THEN** all fields SHALL be accessible as attributes

### Requirement: DLPResult dataclass
The system SHALL define `DLPResult` as a dataclass with fields: `findings` (list[DLPFinding]), `action` (DLPAction), `text` (str | None), `audit_data` (list[dict]).

#### Scenario: Empty findings
- **WHEN** DLPScanner.scan() detects no sensitive entities
- **THEN** `DLPResult(findings=[], action=DLPAction.ALLOW, text=<original>, audit_data=[])` SHALL be returned

#### Scenario: BLOCK action
- **WHEN** any finding resolves to BLOCK action
- **THEN** `DLPResult.text` SHALL be `None` (content withheld)

#### Scenario: MASK action
- **WHEN** any finding resolves to MASK action (no BLOCK)
- **THEN** `DLPResult.text` SHALL contain the masked text with placeholders replaced

### Requirement: DLPRecognizer abstract base class
The system SHALL define `DLPRecognizer` ABC with class attributes `name` (str) and `supported_entities` (list[str]), and abstract method `analyze(text: str, entities: list[str] | None = None) -> list[DLPFinding]`.

#### Scenario: Cannot instantiate directly
- **WHEN** `DLPRecognizer()` is called
- **THEN** `TypeError` SHALL be raised

#### Scenario: Subclass with implementation succeeds
- **WHEN** a class inherits from `DLPRecognizer` and implements `analyze`
- **THEN** the class CAN be instantiated

### Requirement: DLPRecognizerRegistry
The system SHALL define `DLPRecognizerRegistry` with methods `register(recognizer)`, `unregister(name)`, and `analyze(text, entities)`.

#### Scenario: Registry runs all recognizers
- **WHEN** registry has 3 recognizers registered
- **THEN** `analyze(text)` SHALL run all 3 recognizers and merge results

#### Scenario: Registry deduplicates overlapping findings
- **WHEN** two recognizers detect overlapping ranges
- **THEN** `analyze()` SHALL keep the finding with the higher score

#### Scenario: Filter by entity types
- **WHEN** `analyze(text, entities=["EMAIL"])` is called
- **THEN** recognizers SHALL only check the EMAIL entity type

### Requirement: DLPScanner three-layer orchestration
The system SHALL define `DLPScanner` with constructor `(registry: DLPRecognizerRegistry, policy: DLPPolicyResolver)` and method `scan(text: str, direction: str, context: dict | None = None) -> DLPResult`.

#### Scenario: No findings
- **WHEN** scan finds no sensitive entities
- **THEN** return `DLPResult(action=ALLOW, text=<original>)`

#### Scenario: MASK applied
- **WHEN** scan finds EMAIL entities and and policy says MASK for EMAIL on `direction`
- **THEN** return `DLPResult(action=MASK, text=<text with EMAIL masked to [EMAIL]>)`

#### Scenario: BLOCK applied
- **WHEN** scan finds AWS_KEY entities and and policy says BLOCK
- **THEN** return `DLPResult(action=BLOCK, text=None)`

#### Scenario: AUDIT mode
- **WHEN** scan finds EMAIL entities and and policy says AUDIT
- **THEN** return `DLPResult(action=AUDIT, text=<original>)` with `audit_data` populated

