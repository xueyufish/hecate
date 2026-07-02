## ADDED Requirements

### Requirement: TaskAllocator ABC defines agent selection interface
The engine SHALL define a `TaskAllocator` ABC in `engine/task_allocator.py` with abstract method `allocate` that accepts a task description, a list of candidate agents, and optional configuration, returning the best-fit agent or None.

#### Scenario: Allocate with matching candidate
- **WHEN** `allocate(task="Analyze financial report", candidates=[agent_a, agent_b])` is called and agent_a is a finance specialist
- **THEN** the allocator SHALL return agent_a

#### Scenario: No suitable candidate
- **WHEN** `allocate(task="Translate to Japanese", candidates=[finance_agent, legal_agent])` is called and neither matches
- **THEN** the allocator SHALL return None

#### Scenario: create_if_not_found reserved for P3
- **WHEN** `allocate(task="...", candidates=[], create_if_not_found=True)` is called
- **THEN** the P2 implementation SHALL raise `NotImplementedError` with message indicating this is reserved for P3 dynamic agent creation

### Requirement: SemanticTaskAllocator uses LLM for matching
A `SemanticTaskAllocator` SHALL implement TaskAllocator by calling `port.llm_invoke()` to analyze task descriptions against candidate agent descriptions, producing a scored ranking.

#### Scenario: Semantic matching
- **WHEN** `allocate(task="Review legal contract", candidates=[billing_agent, legal_agent, tech_agent])` is called
- **THEN** the allocator SHALL call `port.llm_invoke()` with a prompt containing the task description and all candidate descriptions, parse the LLM response to extract the best match, and return legal_agent

#### Scenario: LLM response parsing
- **WHEN** the LLM returns a structured response with agent rankings
- **THEN** the allocator SHALL parse the response and return the top-ranked agent

#### Scenario: LLM failure fallback
- **WHEN** `port.llm_invoke()` raises an exception during allocation
- **THEN** the allocator SHALL log the error and return None (not raise)

### Requirement: RoundRobinTaskAllocator for simple cases
A `RoundRobinTaskAllocator` SHALL implement TaskAllocator by cycling through candidates in order, suitable for load-balancing scenarios.

#### Scenario: Round-robin selection
- **WHEN** `allocate(task="task_1", candidates=[a, b, c])` is called three times
- **THEN** it SHALL return a, b, c in order, then cycle back to a

#### Scenario: Empty candidates
- **WHEN** `allocate(task="...", candidates=[])` is called
- **THEN** it SHALL return None
