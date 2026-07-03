## ADDED Requirements

### Requirement: EvaluatorABC interface
The system SHALL define an `EvaluatorABC` abstract base class in `src/hecate/plugin/spi/evaluator.py` that evaluation plugins MUST implement. The interface MUST include:
- `name: str` (property) — short identifier for the metric
- `description: str` (property) — human-readable description
- `evaluate(input: EvalInput) -> EvalOutput` (method) — async evaluation logic

#### Scenario: Create evaluator plugin
- **WHEN** a developer creates a class inheriting from EvaluatorABC
- **THEN** the class must implement name, description, and evaluate()

#### Scenario: Attempt to instantiate abstract evaluator
- **WHEN** a developer tries to instantiate EvaluatorABC directly
- **THEN** a TypeError is raised (abstract class cannot be instantiated)

### Requirement: BuiltinEvaluator adapter
The system SHALL refactor the existing `Evaluator(ABC)` in `services/evaluation/evaluator.py` to become `BuiltinEvaluator(EvaluatorABC)`. All existing 41 evaluator subclasses MUST continue working without modification.

#### Scenario: Existing evaluator subclass works
- **WHEN** an existing evaluator subclass (e.g., FaithfulnessEvaluator) is instantiated
- **THEN** it works identically to before the refactoring

#### Scenario: BuiltinEvaluator inherits from EvaluatorABC
- **WHEN** a developer inspects BuiltinEvaluator
- **THEN** it is a subclass of EvaluatorABC and satisfies the evaluator interface

### Requirement: Evaluator registration via PluginRegistry
The system SHALL register all 41 built-in evaluators with PluginRegistry at startup, using type="evaluator".

#### Scenario: All evaluators registered
- **WHEN** the application starts
- **THEN** all 41 built-in evaluators are registered in PluginRegistry under type="evaluator"

#### Scenario: Lookup evaluator by name
- **WHEN** a developer calls `registry.get_by_name("evaluator", "faithfulness")`
- **THEN** the FaithfulnessEvaluator instance is returned

#### Scenario: List all evaluators
- **WHEN** a developer calls `registry.get_by_type("evaluator")`
- **THEN** a dictionary of all 41 evaluators keyed by name is returned
