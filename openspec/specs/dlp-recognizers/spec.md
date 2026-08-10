# dlp-recognizers Specification

## Purpose
TBD - created by archiving change outbound-dlp-engine. Update Purpose after archive.
## Requirements
### Requirement: RegexRecognizer implementation
The system SHALL define `RegexRecognizer(DLPRecognizer)` that detects entities using regex patterns, supporting entity_type → compiled regex mapping, optional Luhn validation for credit cards, and configurable confidence scores per entity.

#### Scenario: Email detection
- **WHEN** RegexRecognizer with pattern `email` analyzes text `"Contact john@example.com"`
- **THEN** it SHALL return `[DLPFinding(entity_type="EMAIL", value="john@example.com", score=0.9, recognizer="regex_builtin")]`

#### Scenario: Credit card with Luhn validation
- **WHEN** RegexRecognizer with credit_card pattern analyzes `"Card: 4532-1234-5678-9010"`
- **THEN** it SHALL return the finding only if the number passes Luhn check
- **AND** it SHALL NOT return the finding if Luhn check fails

#### Scenario: No match
- **WHEN** text contains no pattern matches
- **THEN** `analyze()` SHALL return `[]`

### Requirement: SecretsRecognizer implementation
The system SHALL define `SecretsRecognizer(DLPRecognizer)` that wraps `detect-secrets` library to detect API keys, JWT tokens, private keys, and connection strings.

#### Scenario: AWS key detection
- **WHEN** SecretsRecognizer analyzes text containing `"AKIA1234567890ABCDEF"`
- **THEN** it SHALL return `[DLPFinding(entity_type="AWS_ACCESS_KEY", ...)]`

#### Scenario: JWT detection
- **WHEN** text contains a well-formed JWT (header.payload.signature)
- **THEN** it SHALL return `[DLPFinding(entity_type="JWT_TOKEN", score=0.95)]`

#### Scenario: Library not installed
- **WHEN** `detect-secrets` is not installed
- **THEN** SecretsRecognizer.analyze() SHALL return `[]` and log a warning

### Requirement: PresidioRecognizer implementation
The system SHALL define `PresidioRecognizer(DLPRecognizer)` that wraps Microsoft Presidio AnalyzerEngine for NER-based detection (context-aware). It SHALL be optional — only available if `presidio-analyzer` is installed.

#### Scenario: NER detection
- **WHEN** PresidioRecognizer analyzes text containing a person's name
- **THEN** it SHALL return `[DLPFinding(entity_type="PERSON", score=0.85, recognizer="presidio")]` from spaCy model

#### Scenario: Presidio not installed
- **WHEN** `presidio-analyzer` is not installed
- **THEN** `PresidioRecognizer.__init__()` SHALL raise `ImportError` with installation instructions

#### Scenario: Lazy model loading
- **WHEN** PresidioRecognizer is constructed
- **THEN** spaCy model SHALL be loaded on first `analyze()` call, not at construction

### Requirement: DictionaryRecognizer implementation
The system SHALL define `DictionaryRecognizer(DLPRecognizer)` that detects entities via exact-match against a configurable dictionary of terms.

#### Scenario: Term detection
- **WHEN** DictionaryRecognizer with terms `["ProjectPhoenix", "Sentinel"]` analyzes text mentioning `"ProjectPhoenix"`
- **THEN** it SHALL return `[DLPFinding(entity_type="INTERNAL_PROJECT", value="ProjectPhoenix", score=1.0)]`

#### Scenario: Case-insensitive matching
- **WHEN** `case_sensitive=False` and text mentions `"PROJECTPHOENIX"`
- **THEN** it SHALL match "ProjectPhoenix" from the dictionary

#### Scenario: No match
- **WHEN** text contains no dictionary terms
- **THEN** `analyze()` SHALL return `[]`

### Requirement: RecognizerRegistryFactory
The system SHALL define `DLPRegistryFactory.create()` that builds a DLPRecognizerRegistry from a database session, loading built-in Recognizers (Regex, Secrets, Presidio if available) and user-defined custom regex/dictionary entries.

#### Scenario: Load built-in recognizers
- **WHEN** `create()` is called
- **THEN** registry SHALL contain RegexRecognizer and SecretsRecognizer

#### Scenario: Load custom regex
- **WHEN** DB has `DLPCustomRegexModel(entity_type="EMPLOYEE_ID", pattern="EMP-\d{6}")` for the org
- **THEN** registry SHALL contain a custom RegexRecognizer detecting EMPLOYEE_ID

#### Scenario: Load custom dictionary
- **WHEN** DB has `DLPDictionaryModel(name="internal_projects", terms=["Alpha", "Beta"])` for the org
- **THEN** registry SHALL contain a DictionaryRecognizer for INTERNAL_PROJECT

