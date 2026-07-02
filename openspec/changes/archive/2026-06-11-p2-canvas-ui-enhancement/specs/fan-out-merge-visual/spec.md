## MODIFIED Requirements

### Requirement: Fan-out and merge are not in the node palette
The NodePalette component SHALL include fan-out and merge as draggable items. These node types CAN be created interactively by dragging from the palette, in addition to appearing when loaded from an existing DSL.

#### Scenario: Node palette contents
- **WHEN** the workflow editor loads
- **THEN** the node palette shows 8 items: Conversation, Condition, Tool Call, Agent, Knowledge Retrieval, Variable Set, Fan Out, Merge
