## MODIFIED Requirements

### Requirement: Form submission
The form SHALL submit all configured fields to the API. In create mode, it SHALL POST to `/api/agents`. In edit mode, it SHALL PUT to `/api/agents/{id}`. On success, it SHALL navigate to the agent detail page. If the API returns a 400 error for invalid `knowledge_base_ids`, the form SHALL display the validation error near the Knowledge Bases selector.

#### Scenario: Successful creation
- **WHEN** the user fills in required fields and clicks "Create"
- **THEN** the system SHALL POST to `/api/agents` and navigate to `/agents/{new_id}`

#### Scenario: Successful update
- **WHEN** the user modifies fields and clicks "Save"
- **THEN** the system SHALL PUT to `/api/agents/{id}` and show a success message

#### Scenario: Submission error
- **WHEN** the API returns an error
- **THEN** the system SHALL display the error message and keep the form data intact

#### Scenario: Invalid KB IDs error
- **WHEN** the API returns 400 with invalid `knowledge_base_ids`
- **THEN** the system SHALL display the error message near the Knowledge Bases selector and keep the form data intact

### Requirement: Knowledge tab fields
The Knowledge tab SHALL contain: Knowledge Bases (multi-select from available KBs) and Skills (multi-select from available skills). Deleted KBs SHALL NOT appear in the selector options.

#### Scenario: Knowledge base selection
- **WHEN** the user opens the Knowledge Bases selector
- **THEN** the system SHALL display all available (non-deleted) knowledge bases and allow multi-select

#### Scenario: Skill selection
- **WHEN** the user opens the Skills selector
- **THEN** the system SHALL display all available skills and allow multi-select

#### Scenario: Empty state when no KBs/skills exist
- **WHEN** there are no knowledge bases or skills configured
- **THEN** the system SHALL display an empty state message with a link to create one
