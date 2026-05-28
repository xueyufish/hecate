## ADDED Requirements

### Requirement: Create memory block
The system SHALL provide an API endpoint `POST /api/agents/{agent_id}/memory-blocks` that creates a new memory block for an agent.

#### Scenario: Successful creation
- **WHEN** a user sends a POST request with label, content, position, and limit
- **THEN** the system creates a MemoryBlockModel and returns 201 with the block data

#### Scenario: Duplicate label
- **WHEN** a user sends a POST request with a label that already exists for the agent
- **THEN** the system returns 409 Conflict

### Requirement: Read memory block
The system SHALL provide an API endpoint `GET /api/agents/{agent_id}/memory-blocks/{block_id}` that returns a memory block.

#### Scenario: Block exists
- **WHEN** a user sends a GET request with valid agent_id and block_id
- **THEN** the system returns 200 with the block data

#### Scenario: Block not found
- **WHEN** a user sends a GET request for a non-existent block
- **THEN** the system returns 404

### Requirement: Update memory block
The system SHALL provide an API endpoint `PUT /api/agents/{agent_id}/memory-blocks/{block_id}` that updates a memory block's content.

#### Scenario: Successful update
- **WHEN** a user sends a PUT request with updated content
- **THEN** the system updates the block and returns 200

### Requirement: Delete memory block
The system SHALL provide an API endpoint `DELETE /api/agents/{agent_id}/memory-blocks/{block_id}` that deletes a memory block.

#### Scenario: Successful deletion
- **WHEN** a user sends a DELETE request
- **THEN** the system deletes the block and returns 204

### Requirement: List memory blocks
The system SHALL provide an API endpoint `GET /api/agents/{agent_id}/memory-blocks` that returns all memory blocks for an agent.

#### Scenario: List blocks
- **WHEN** a user sends a GET request
- **THEN** the system returns 200 with all blocks ordered by position

### Requirement: Memory blocks in context assembly
The system SHALL include memory blocks in the context assembly process when building the LLM prompt.

#### Scenario: Blocks included in context
- **WHEN** context is assembled for an agent with memory blocks
- **THEN** the blocks SHALL be inserted into the messages array at their configured positions, respecting their token limits

#### Scenario: No blocks configured
- **WHEN** an agent has no memory blocks
- **THEN** the context assembly SHALL proceed without memory blocks
