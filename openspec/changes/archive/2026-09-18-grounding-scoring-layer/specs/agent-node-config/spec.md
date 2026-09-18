## ADDED Requirements

### Requirement: Grounding scoring configuration per node

Agent node configuration SHALL accept a grounding scoring policy: an enable flag (default disabled), the scoring backend selection (`llm_judge` or an HTTP NLI endpoint definition), the trigger condition (`on_uncited` default, `always`, or a sample rate), disposition threshold fields used for the shadow disposition, a fallback retrieval section (enable flag plus dedicated fallback knowledge base IDs, independent of the node's retrieval `kb_ids`), and escalation settings for upgrading borderline pairs to the judge backend. The policy SHALL be validated at load time: unknown fields and invalid values SHALL be rejected with an error identifying the invalid field, mirroring the citation provenance policy semantics. The fully resolved policy SHALL contribute to the node's canonical policy hash.

#### Scenario: Node enables scoring with LLM judge backend

- **WHEN** a node configures grounding scoring with `backend: llm_judge` and the default trigger
- **THEN** responses of that node SHALL be scored per the grounding scoring behavior, with verdicts recorded in audit events

#### Scenario: Node configures HTTP NLI endpoint with fallback

- **WHEN** a node selects the HTTP NLI backend with an endpoint definition and enables fallback retrieval with explicit knowledge base IDs
- **THEN** scoring SHALL call the configured endpoint for (claim, evidence) pairs and uncited claims SHALL be scored against retrieval from the fallback knowledge bases

#### Scenario: Unknown field rejected

- **WHEN** a grounding scoring policy contains a field outside the supported set
- **THEN** configuration SHALL fail at load time with an error naming the unknown field

#### Scenario: Default is disabled

- **WHEN** a node does not configure grounding scoring
- **THEN** no scoring, backend calls, or scoring events SHALL occur for that node, and behavior SHALL be identical to a platform without the feature
