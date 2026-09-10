## Purpose

Lets users generate evaluation datasets from seed items, topic descriptions, or adversarial intents via an LLM-driven synthesis pipeline. Synthesized items land in a new dataset with `tags` for provenance so the Evaluation Engine can score them like any other item.

## ADDED Requirements

### Requirement: Synthesis request shape
The system SHALL accept synthesis requests via `POST /evaluation/datasets/synthesize` with body fields: `seed_dataset_id: UUID | None`, `topic: str | None`, `strategy: Literal["generation", "evolution", "adversarial"]`, `adversarial_intent: Literal["prompt_injection_basic", "prompt_injection_indirect", "jailbreak_dan_style", "pii_extraction", "jailbreak_roleplay"] | None`, `count: int` (1-100), `target_dataset_name: str`, `target_dataset_description: str | None`, `llm_config: dict | None`. The system SHALL reject requests with no seed source AND no topic AND no adversarial intent with 400 Bad Request.

#### Scenario: Generation strategy with seed dataset
- **WHEN** the request contains `strategy="generation"`, `seed_dataset_id=<existing>`, `count=20`, `target_dataset_name="gen-test-v1"`
- **THEN** the system SHALL derive Q&A pairs from the seed dataset items and write them to a new dataset named "gen-test-v1"

#### Scenario: Generation strategy with topic text
- **WHEN** the request contains `strategy="generation"`, `topic="enterprise SSO troubleshooting"`, `count=30`, `target_dataset_name="sso-synth"`
- **THEN** the system SHALL generate Q&A pairs grounded in the topic and write them to a new dataset named "sso-synth"

#### Scenario: Adversarial strategy requires intent
- **WHEN** the request contains `strategy="adversarial"` and no `adversarial_intent`
- **THEN** the system SHALL return 400 Bad Request with an explanatory message

#### Scenario: Count bounds
- **WHEN** the request contains `count=0` or `count>100`
- **THEN** the system SHALL return 400 Bad Request

### Requirement: Synchronous synthesis for small batches
The system SHALL execute synthesis synchronously when `count <= 20`. The endpoint SHALL return `200 OK` with body `{ "target_dataset_id": UUID, "items_generated": int, "items_filtered": int, "duration_ms": int }` after all items are written.

#### Scenario: Synchronous generation of 10 items
- **WHEN** the request contains `count=10` and the synthesis pipeline produces 10 items after filtering
- **THEN** the endpoint SHALL return within 60 seconds and the body SHALL contain `items_generated=10` and `items_filtered=0`

#### Scenario: Synchronous generation with filtered items
- **WHEN** the request contains `count=15` and 3 items are dropped by quality/dedupe/DLP filters
- **THEN** the body SHALL contain `items_generated=12` and `items_filtered=3`, and only 12 items SHALL be persisted to the target dataset

### Requirement: Asynchronous synthesis for large batches
The system SHALL execute synthesis asynchronously when `count > 20`. The endpoint SHALL return `202 Accepted` with body `{ "job_id": UUID, "status": "queued" }`. The system SHALL provide `GET /evaluation/synthesis-jobs/{job_id}` returning the current status (`queued` / `running` / `completed` / `failed`), counts, error message, and target dataset ID.

#### Scenario: Asynchronous generation of 50 items
- **WHEN** the request contains `count=50`
- **THEN** the endpoint SHALL return 202 with `job_id` and `status="queued"`; the client polls `GET /evaluation/synthesis-jobs/{job_id}` to retrieve progress

#### Scenario: Failed synthesis job
- **WHEN** a synthesis job encounters an unrecoverable LLM error mid-pipeline
- **THEN** the job status SHALL transition to `failed` and `GET /evaluation/synthesis-jobs/{job_id}` SHALL return the error message; partial items already written remain in the target dataset

### Requirement: Generation strategy
The system SHALL provide a `GenerationStrategy` that, given seed items or topic text, prompts an LLM to produce `(query, expected_answer, context)` triples. Each generation request SHALL be a single LLM call. The strategy SHALL be concurrent across items with a configurable concurrency cap (default 5) respecting the `llm_gateway` rate limits.

#### Scenario: Generation from seed items
- **WHEN** the strategy receives a seed item with `query="What is RAG?"` and `expected_answer="Retrieval Augmented Generation"`
- **THEN** the LLM SHALL produce a paraphrased Q&A triple that preserves the original intent but uses different wording

#### Scenario: Generation from topic only
- **WHEN** the strategy receives topic text with no seed items
- **THEN** the LLM SHALL produce `count` distinct Q&A triples covering different facets of the topic

### Requirement: Evolution strategy
The system SHALL provide an `EvolutionStrategy` that takes seed items and applies one of three evolution types sampled per item: `REASONING` (adds logical-reasoning requirement), `HYPOTHETICAL` (frames as hypothetical scenario), `IN_BREADTH` (broadens scope). Each evolution request SHALL be a single LLM call. The strategy SHALL NOT use the four RAG-only evolution types (`MULTICONTEXT` / `CONCRETIZING` / `CONSTRAINED` / `COMPARATIVE`) since 7.2b v1 does not anchor items to context.

#### Scenario: Evolution REASONING type
- **WHEN** the strategy evolves a seed item with `evolution_type="REASONING"`
- **THEN** the evolved query SHALL require the answerer to perform logical deduction rather than recall a fact

#### Scenario: Evolution type sampling
- **WHEN** the strategy processes 10 seed items
- **THEN** the strategy SHALL sample evolution types uniformly across `REASONING` / `HYPOTHETICAL` / `IN_BREADTH`, producing ~3-4 items per type

### Requirement: Adversarial strategy
The system SHALL provide an `AdversarialStrategy` that combines an intent (the malicious goal) with a base transformation (the evasion technique). v1 SHALL support 5 intents × 1 base transformation each: `prompt_injection_basic` (instruction-override wrapper), `prompt_injection_indirect` (data-channel injection), `jailbreak_dan_style` (roleplay override), `pii_extraction` (data-exfil prompts), `jailbreak_roleplay` (persona-based jailbreak). The OWASP LLM Top-10 reference SHALL be the taxonomy anchor (matching the existing `injection-detection` recognizers in `runtime/security/hooks`).

#### Scenario: Adversarial with prompt injection intent
- **WHEN** the request specifies `adversarial_intent="prompt_injection_basic"`
- **THEN** the strategy SHALL generate items whose queries contain injection attempts that try to override the system prompt

#### Scenario: Adversarial with PII extraction intent
- **WHEN** the request specifies `adversarial_intent="pii_extraction"`
- **THEN** the strategy SHALL generate items whose queries try to extract PII patterns aligned with the existing DLP regex dictionary

### Requirement: Embedding deduplication filter
The system SHALL provide an `EmbeddingDedupeFilter` that computes cosine similarity between candidate items and existing dataset items. Items with similarity `>= 0.92` to any existing item SHALL be dropped. The filter SHALL use `hecate_memory.rag.embedding.embedding_service.encode()` for embedding generation. When the embedding service is unavailable (FlagEmbedding missing) or running in mock mode, the filter SHALL fall back to character-level n-gram Jaccard similarity with threshold `0.85`.

#### Scenario: Embedding-based dedupe drops near-duplicate
- **WHEN** a candidate item has cosine similarity 0.95 to an existing dataset item
- **THEN** the filter SHALL drop the candidate item

#### Scenario: Fallback to n-gram when embedding is mock
- **WHEN** `embedding_service._get_model()` returns `"mock"`
- **THEN** the filter SHALL use character n-gram Jaccard similarity instead of cosine

#### Scenario: Empty dataset has no dedupe effect
- **WHEN** the target dataset has no existing items
- **THEN** the filter SHALL pass all candidate items through without comparison

### Requirement: Quality filter via critic LLM
The system SHALL provide a `QualityFilter` that prompts a critic LLM to score each candidate item on two dimensions: `self_containment` (0.0-1.0, the query is answerable without external context) and `clarity` (0.0-1.0, the query is unambiguous). Items scoring below `0.6` on either dimension SHALL be dropped. The threshold SHALL be configurable per request (`quality_threshold: float | None`); default 0.6.

#### Scenario: Low-quality item dropped
- **WHEN** a candidate item scores 0.4 on self_containment from the critic
- **THEN** the filter SHALL drop the candidate item

#### Scenario: All items pass quality filter
- **WHEN** all candidate items score >= 0.6 on both dimensions
- **THEN** the filter SHALL pass all items through with `items_filtered=0`

### Requirement: DLP filter before persistence
The system SHALL provide a `DLPFilter` that runs `DLPService.scan()` on each candidate item's `query`, `expected_answer`, and `context` content. Items where DLP flags the content SHALL be dropped with a `DLP blocked` error classification (distinct from `LLM generation failed`). The filter SHALL use the workspace's active DLP policies.

#### Scenario: DLP blocks item containing credit card number
- **WHEN** a candidate item's `expected_answer` contains text matching the credit-card regex policy
- **THEN** the filter SHALL drop the item and increment `items_filtered`; the failure reason SHALL be recorded as `dlp_blocked`

#### Scenario: DLP passes clean content
- **WHEN** no DLP policy matches any content in the candidate item
- **THEN** the filter SHALL pass the item through

### Requirement: Synthesized items carry tags
The system SHALL set the `tags` JSON column on every synthesized `EvaluationItemModel` to include at minimum: `"synthetic"`, the strategy name (`"strategy:generation"` / `"strategy:evolution"` / `"strategy:adversarial"`), and where applicable `"intent:<adversarial_intent>"` or `"seed_dataset:<id>"`. The tags SHALL be persisted at the ORM level (not just in metadata JSON).

#### Scenario: Tags on generation output
- **WHEN** a `generation`-strategy synthesis completes
- **THEN** every persisted item SHALL have `tags=["synthetic", "strategy:generation", "seed_dataset:<uuid>"]`

#### Scenario: Tags on adversarial output
- **WHEN** an `adversarial`-strategy synthesis with `adversarial_intent="prompt_injection_basic"` completes
- **THEN** every persisted item SHALL have `tags=["synthetic", "strategy:adversarial", "intent:prompt_injection_basic"]`

### Requirement: Synthesized items support tag-filtered evaluation runs
The system SHALL filter evaluation items by tag when the run request specifies `tags: list[str] | None`. The filter SHALL match items whose `tags` JSON array contains ANY of the specified tags (OR semantics). This allows running an evaluation against only synthesized adversarial items.

#### Scenario: Run only adversarial items
- **WHEN** an evaluation run request specifies `tags=["intent:prompt_injection_basic"]`
- **THEN** the engine SHALL score only items whose tags include that value

#### Scenario: Run all items when tags omitted
- **WHEN** the run request omits `tags`
- **THEN** the engine SHALL score all items in the dataset regardless of tags

### Requirement: Synthesis pipeline is auditable
The system SHALL record synthesis job config (`strategy`, `intents`, `count`, `seed_dataset_id`, `topic`, `llm_config`, `quality_threshold`) in the `DatasetSynthesisJobModel.config` JSON column. The system SHALL record `items_generated`, `items_filtered`, and per-filter failure counts in the `metrics` JSON column on job completion.

#### Scenario: Job config persists
- **WHEN** a synthesis job completes
- **THEN** `GET /evaluation/synthesis-jobs/{job_id}` SHALL return the original config and final metrics
