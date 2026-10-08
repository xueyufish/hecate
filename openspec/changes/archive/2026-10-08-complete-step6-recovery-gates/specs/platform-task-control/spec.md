## ADDED Requirements

### Requirement: Platform associates successor attempts before accepting successor facts

When a task resumes from a persistent wait, the platform SHALL record the successor attempt association before folding successor execution events into the Task projection. Event ingestion SHALL reject or quarantine successor events whose attempt association is missing, mismatched, or belongs to another workspace. Historical Run queries SHALL remain scoped to their own attempt facts.

#### Scenario: Successor event without association is quarantined

- **WHEN** the platform receives a running or terminal event for a successor Run that has no recorded association
- **THEN** the event is quarantined for reconciliation and does not change the Task projection

### Requirement: Workflow parent-child callback uses a named adapter and persisted facts

The platform SHALL connect the existing parent-child wait/callback primitive to a deterministic workflow node or named adapter. The callback SHALL verify the child Task exists in the same workspace, reached the declared terminal state, matches the parent waiting contract, and presents a valid one-time wait token before waking the parent. Parent and child facts SHALL survive independent process restarts.

#### Scenario: Real adapter callback wakes the parent after child terminal fact

- **WHEN** a workflow parent waits for a child through the named adapter and the child reaches succeeded
- **THEN** the callback verifies the persisted child terminal fact, consumes the parent wait token once, and requeues the parent with the child result summary

#### Scenario: Cross-workspace or forged child callback is rejected

- **WHEN** a callback references a child from another workspace or a child whose terminal fact does not match the declaration
- **THEN** the command receipt is rejected, the parent wait token is not consumed, and the parent remains waiting
