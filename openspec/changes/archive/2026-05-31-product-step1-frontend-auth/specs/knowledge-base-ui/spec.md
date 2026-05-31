## ADDED Requirements

### Requirement: Knowledge base list
The system SHALL display all knowledge bases with name, document count, and creation date.

#### Scenario: View knowledge base list
- **WHEN** user navigates to Knowledge Bases page
- **THEN** system shows a list of knowledge bases with name, document count, and a "Create" button

### Requirement: Create knowledge base
The system SHALL allow users to create a knowledge base with name and description.

#### Scenario: Successful creation
- **WHEN** user fills in name and optional description and clicks Create
- **THEN** system creates the knowledge base via `POST /api/knowledge-bases` and redirects to detail page

### Requirement: Upload documents
The system SHALL allow users to upload documents to a knowledge base.

#### Scenario: Upload single document
- **WHEN** user selects a file (PDF, DOCX, TXT, MD) and clicks Upload
- **THEN** system uploads via `POST /api/knowledge-bases/{id}/upload` and shows upload progress

#### Scenario: Supported formats
- **WHEN** user selects an unsupported file type
- **THEN** system shows a validation error indicating supported formats

### Requirement: Document status display
The system SHALL show parsing status for each uploaded document.

#### Scenario: View document list
- **WHEN** user opens a knowledge base detail page
- **THEN** system shows all documents with filename, file size, parsing status (pending/parsing/completed/failed), and chunk count

#### Scenario: Failed document
- **WHEN** a document has parsing_status "failed"
- **THEN** system displays the error message from parsing_error field

### Requirement: Delete knowledge base
The system SHALL allow users to delete a knowledge base with confirmation.

#### Scenario: Delete with confirmation
- **WHEN** user clicks delete and confirms
- **THEN** system deletes the knowledge base and redirects to list page
