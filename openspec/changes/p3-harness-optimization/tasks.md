## 1. Failure Analysis

- [x] 1.1 Create `services/harness/failure_analyzer.py` with FailureAnalyzer class
- [x] 1.2 Implement classify_failure(trajectory) — LLM-based failure classification
- [x] 1.3 Implement analyze_root_cause(trajectory) — root cause analysis
- [x] 1.4 Define 10 failure types (reference AgentRx taxonomy)

## 2. Constraint Generation

- [x] 2.1 Create `services/harness/constraint_generator.py` with ConstraintGenerator class
- [x] 2.2 Implement generate_constraint(failure_analysis) — generate constraint rule
- [x] 2.3 Implement constraint rule format — trigger, action, priority

## 3. Constraint Injection

- [x] 3.1 Create `services/harness/constraint_injector.py` with ConstraintInjector class
- [x] 3.2 Implement inject_constraints(messages, session_id) — append constraints to system prompt
- [x] 3.3 Implement constraint prioritization — order by priority

## 4. Integration

- [x] 4.1 Integrate with ContextAssembler — inject constraints during assembly
- [x] 4.2 Integrate with EvidenceTracker — trigger analysis on failures

## 5. Testing

- [x] 5.1 Unit tests for FailureAnalyzer — classification, root cause
- [x] 5.2 Unit tests for ConstraintGenerator — generation, format
- [x] 5.3 Unit tests for ConstraintInjector — injection, prioritization
