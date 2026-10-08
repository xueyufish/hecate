## ADDED Requirements

### Requirement: Cursor pages declare unread observed events
The durable event log SHALL expose whether observed events remain after the returned cursor, including when gap markers consume page capacity.

#### Scenario: Page ends at a gap marker
- **WHEN** a gap marker fills the page before the next observed event
- **THEN** has_more is true and the next page returns that observed event

### Requirement: Run state evidence is scoped to the attempt
The durable event log SHALL retrieve the latest state evidence for a complete Run reference and its own source.

#### Scenario: Task advances to a successor Run
- **WHEN** the Task completes in a successor attempt
- **THEN** historical state evidence remains scoped to the original Run
