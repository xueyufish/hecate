## MODIFIED Requirements

### Requirement: Built-in evaluator scope and naming
The system SHALL ship 16 built-in evaluators organized by scope into four categories: Result Layer (output quality, 5 evaluators), Process Layer (tool and reasoning correctness, 2 evaluators), RAG Layer (retrieval-augmented generation quality, 4 evaluators), and Safety Layer (security and compliance, 5 evaluators). Each evaluator SHALL declare its scope via a `scope` class attribute taking one of `"result"` / `"process"` / `"rag"` / `"safety"`. The system SHALL use the canonical short names listed below as both the registry key and the `name` property of each evaluator class.

| Scope | Canonical Name | Type | Source |
|-------|----------------|------|--------|
| result | `correctness` | llm_judge | hecate_llm |
| result | `relevancy` | llm_judge | hecate_llm |
| result | `completeness` | llm_judge | hecate_llm |
| result | `contains` | deterministic | in-process |
| result | `exact_match` | deterministic | in-process |
| result | `is_json` | deterministic | in-process |
| result | `regex_match` | deterministic | in-process |
| process | `tool_call_accuracy` | llm_judge | hecate_llm |
| process | `task_completion` | llm_judge | hecate_llm |
| rag | `context_precision` | ragas | ragas (optional) |
| rag | `context_recall` | ragas | ragas (optional) |
| rag | `faithfulness` | ragas | ragas (optional) |
| rag | `answer_relevancy` | ragas | ragas (optional) |
| safety | `refusal` | llm_judge | hecate_llm |
| safety | `harmfulness` | llm_judge | hecate_llm |
| safety | `pii_leakage` | deterministic | in-process |

#### Scenario: List evaluators grouped by scope
- **WHEN** `GET /api/evaluation/evaluators` is called without filter
- **THEN** the system SHALL return all 16 evaluators grouped by their `scope` value, each entry including `name`, `description`, `scope`, `source` (`deterministic` / `llm_judge` / `ragas`), and required `EvalInput` fields

#### Scenario: Filter evaluators by scope
- **WHEN** `GET /api/evaluation/evaluators?scope=safety` is called
- **THEN** the system SHALL return only evaluators with `scope="safety"` (`refusal`, `harmfulness`, `pii_leakage`)

#### Scenario: Total evaluator count is 16
- **WHEN** the evaluator registry is enumerated at startup
- **THEN** the registry SHALL contain exactly 16 evaluators (5 result + 2 process + 4 rag + 5 safety)

### Requirement: Single registration path via PluginRegistry
The system SHALL register evaluators through exactly one path: the existing `register_evaluators(registry: PluginRegistry)` function in `ops/evaluation/engine.py`, called at application startup. The `PluginRegistry` instance SHALL be the authoritative store, holding both the evaluator instance and a `PluginManifest(type="evaluator", name=<canonical_name>, version="1.0.0", description=<description>)`. The system SHALL additionally expose a module-private class index `_EVALUATOR_CLASS_REGISTRY: dict[str, type[Evaluator]]` written by `register_evaluators` for API consumers that need the class (not instance) for deferred instantiation. The `api/evaluation.py` module SHALL NOT maintain its own parallel dict.

#### Scenario: Single startup registration
- **WHEN** the application starts up via `composition/wiring.py`
- **THEN** `register_evaluators(plugin_registry)` is called exactly once and the `PluginRegistry` SHALL contain all 16 evaluators with type="evaluator"

#### Scenario: API resolves evaluator class via engine.get_evaluator_class
- **WHEN** `POST /evaluation/runs` receives `evaluators=["correctness", "tool_call_accuracy"]`
- **THEN** the endpoint SHALL resolve each name through `engine.get_evaluator_class(name)` (which reads from `_EVALUATOR_CLASS_REGISTRY`) and instantiate them; an unknown name SHALL return 400 with `available` listing the 16 canonical names

#### Scenario: Third-party evaluator registration
- **WHEN** a plugin registers a custom evaluator via `plugin_registry.register(manifest, instance)` with `type="evaluator"`
- **THEN** it SHALL be discoverable via `plugin_registry.get_by_type("evaluator")` and resolvable via `engine.get_evaluator_class(name)` after startup completes

### Requirement: RAG evaluators degrade gracefully when ragas is missing
The system SHALL attempt to import ragas at registration time. When ragas is not installed, the four RAG evaluators (`context_precision`, `context_recall`, `faithfulness`, `answer_relevancy`) SHALL be skipped with a warning log; the remaining 12 evaluators SHALL still register successfully. The `PluginRegistry` SHALL NOT fail to register due to ragas absence.

#### Scenario: ragas installed, all 16 register
- **WHEN** `ragas>=0.2.0` is installed and `register_evaluators(registry)` runs
- **THEN** the registry SHALL contain all 16 evaluators

#### Scenario: ragas missing, 12 register
- **WHEN** `ragas` import raises `ImportError` and `register_evaluators(registry)` runs
- **THEN** the registry SHALL contain exactly 12 evaluators (the 4 rag evaluators skipped); a warning log SHALL be emitted listing the skipped names

### Requirement: Deterministic evaluators have no LLM dependency
The four deterministic evaluators (`contains`, `exact_match`, `is_json`, `regex_match`) plus `pii_leakage` SHALL NOT invoke any LLM call. They SHALL execute in sub-millisecond time on a single item. They SHALL set `Score.source="deterministic"` on their output.

#### Scenario: Contains evaluator with substring match
- **WHEN** the `contains` evaluator is called with `generated_answer="RAG stands for Retrieval Augmented Generation"` and expected substring `"Retrieval"`
- **THEN** it SHALL return a Score with `value=1.0`, `source="deterministic"`, and `metric_name="contains"`

#### Scenario: Exact match evaluator
- **WHEN** the `exact_match` evaluator is called with `generated_answer="Paris"` and `expected_answer="Paris"`
- **THEN** it SHALL return a Score with `value=1.0` and `source="deterministic"`

#### Scenario: Is JSON evaluator with valid JSON
- **WHEN** the `is_json` evaluator is called with `generated_answer='{"key": "value"}'`
- **THEN** it SHALL return a Score with `value=1.0` and `source="deterministic"`

#### Scenario: Regex match evaluator with pattern
- **WHEN** the `regex_match` evaluator is called with `generated_answer="Error code: E-1234"` and pattern `E-\d{4}`
- **THEN** it SHALL return a Score with `value=1.0`

#### Scenario: Deterministic evaluators never invoke LLM
- **WHEN** any of `contains`, `exact_match`, `is_json`, `regex_match`, `pii_leakage` is called
- **THEN** no entry into the `llm_service` chat path SHALL occur (verifiable via test mock)

### Requirement: Safety evaluators anchor on OWASP LLM Top-10
The three safety evaluators (`refusal`, `harmfulness`, `pii_leakage`) SHALL align with the OWASP LLM Top-10 taxonomy already used by the existing `injection-detection` recognizers. `refusal` and `harmfulness` SHALL use LLM-as-Judge prompts following the existing `prompts.py` template style. `pii_leakage` SHALL detect PII patterns using the same regex set as `DLPService` to ensure evaluation output matches runtime DLP behavior.

#### Scenario: Refusal evaluator detects declined response
- **WHEN** the `refusal` evaluator is called with a `generated_answer` that declines the user request
- **THEN** it SHALL return a Score with `value=1.0` (the agent correctly refused) and `source="llm_judge"`

#### Scenario: Harmfulness evaluator flags harmful content
- **WHEN** the `harmfulness` evaluator is called with a `generated_answer` containing insults, hate speech, or stereotyping
- **THEN** it SHALL return a Score with `value=0.0` and `source="llm_judge"`, with reasoning identifying the harmful content

#### Scenario: PII leakage evaluator detects credit card number
- **WHEN** the `pii_leakage` evaluator is called with `generated_answer` containing text matching a credit card regex pattern
- **THEN** it SHALL return a Score with `value=0.0` and `source="deterministic"`

#### Scenario: PII leakage evaluator shares regex set with DLP
- **WHEN** `DLPService` defines a credit card regex and `pii_leakage` runs against the same input
- **THEN** the two SHALL produce matching verdicts (both flag or both pass) — verifiable via test that imports `DLPService` patterns

### Requirement: PluginManifest carries evaluator metadata
The `PluginManifest` used to register each evaluator SHALL include: `type="evaluator"`, `name` (canonical short name), `version="1.0.0"`, `description` (from `cls.description`), `entry="python:hecate.ops.evaluation.<file>:<ClassName>"`. The manifest's `version` field SHALL be `"1.0.0"` for all 16 built-ins in this change.

#### Scenario: Manifest version consistency
- **WHEN** the registry enumerates evaluators and inspects manifests
- **THEN** every built-in evaluator manifest SHALL report `version="1.0.0"`

#### Scenario: Manifest entry points to class
- **WHEN** the system needs to resolve a class for a registered evaluator name (e.g. for tooling that imports classes)
- **THEN** it SHALL follow `manifest.entry` to locate the class
