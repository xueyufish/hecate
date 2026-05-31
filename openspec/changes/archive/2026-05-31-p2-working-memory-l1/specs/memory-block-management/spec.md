## ADDED Requirements

### Requirement: Memory Block Editor Component
The system SHALL provide a `MemoryBlockEditor` component that displays a list of memory blocks for an agent with inline editing capabilities. Each block SHALL show its label, content preview, position, and token limit.

#### Scenario: Display memory blocks
- **WHEN** the Memory tab is opened in the agent configurator
- **THEN** the system SHALL display all memory blocks for the agent, ordered by position, with label, content preview (first 100 chars), and limit shown

#### Scenario: Edit block content inline
- **WHEN** the user clicks on a block's content area
- **THEN** the system SHALL enter edit mode with a textarea, showing Save and Cancel buttons

#### Scenario: Save edited block
- **WHEN** the user edits content and clicks Save
- **THEN** the system SHALL call `PUT /api/agents/{id}/memory-blocks/{block_id}` and update the displayed content

#### Scenario: Cancel editing
- **WHEN** the user clicks Cancel during editing
- **THEN** the system SHALL revert to the original content without API call

#### Scenario: Delete a block
- **WHEN** the user clicks the delete button on a block
- **THEN** the system SHALL show a confirmation dialog, then call `DELETE /api/agents/{id}/memory-blocks/{block_id}`

### Requirement: Memory Block Templates
The system SHALL provide predefined templates for common memory block types. Users SHALL be able to add a template block with one click, which creates the block with suggested content and settings.

#### Scenario: Add template block
- **WHEN** the user selects a template (e.g., "Persona") from the template dropdown
- **THEN** the system SHALL call `POST /api/agents/{id}/memory-blocks` with the template's label, content hint, position, and limit

#### Scenario: Template already exists
- **WHEN** the user selects a template whose label already exists for the agent
- **THEN** the system SHALL show an error message indicating the block already exists

#### Scenario: Available templates
- **WHEN** the template dropdown is opened
- **THEN** the system SHALL display templates: persona, user_profile, domain_context, task_tracker

### Requirement: Create Custom Memory Block
The system SHALL provide a form for creating custom memory blocks with user-defined label, content, position, and limit.

#### Scenario: Open create form
- **WHEN** the user clicks "Add Block" button
- **THEN** the system SHALL display a form with fields: label (required), content, position (default 0), limit (default 2000)

#### Scenario: Submit custom block
- **WHEN** the user fills in the form and clicks Create
- **THEN** the system SHALL call `POST /api/agents/{id}/memory-blocks` and add the new block to the list

#### Scenario: Duplicate label error
- **WHEN** the user submits a block with a label that already exists
- **THEN** the system SHALL display the 409 conflict error message

### Requirement: Memory Block Indicators in Chat
The chat page SHALL display badges showing which memory blocks are active for the current agent conversation. Each badge SHALL show the block label.

#### Scenario: Agent with memory blocks
- **WHEN** a user chats with an agent that has memory blocks
- **THEN** the chat UI SHALL display badges for each active block label near the chat header

#### Scenario: Agent with no memory blocks
- **WHEN** a user chats with an agent that has no memory blocks
- **THEN** the chat UI SHALL NOT display any memory block indicators

### Requirement: Memory Blocks on Agent Detail Page
The agent detail page SHALL display a section showing the agent's memory blocks with their labels and content previews. Users SHALL be able to navigate to the agent configurator's Memory tab from this section.

#### Scenario: Agent detail with blocks
- **WHEN** the user views an agent detail page that has memory blocks
- **THEN** the system SHALL display a "Memory Blocks" section with block labels, content previews, and an "Edit" link to the configurator

#### Scenario: Agent detail with no blocks
- **WHEN** the user views an agent detail page that has no memory blocks
- **THEN** the system SHALL display "No memory blocks configured" with a link to add some
