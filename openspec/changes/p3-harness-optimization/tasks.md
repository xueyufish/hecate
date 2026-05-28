## 1. Failure Analysis

- [ ] 1.1 Create `services/harness/failure_analyzer.py` with FailureAnalyzer class
- [ ] 1.2 Implement classify_failure(trajectory) — LLM-based failure classification
- [ ] 1.3 Implement analyze_root_cause(trajectory) — root cause analysis
- [ ] 1.4 Define 10 failure types (reference AgentRx taxonomy)

## 2. Constraint Generation

- [ ] 2.1 Create `services/harness/constraint_generator.py` with ConstraintGenerator class
- [ ] 2.2 Implement generate_constraint(failure_analysis) — generate constraint rule
- [ ] 2.3 Implement constraint rule format — trigger, action, priority

## 3. Constraint Injection

- [ ] 3.1 Create `services/harness/constraint_injector.py` with ConstraintInjector class
- [ ] 3.2 Implement inject_constraints(messages, session_id) — append constraints to system prompt
- [ ] 3.3 Implement constraint prioritization — order by priority

## 4. Integration

- [ ] 4.1 Integrate with ContextAssembler — inject constraints during assembly
- [ ] 4.2 Integrate with EvidenceTracker — trigger analysis on failures

## 5. Testing

- [ ] 5.1 Unit tests for FailureAnalyzer — classification, root cause
- [ ] 5.2 Unit tests for ConstraintGenerator — generation, format
- [ ] 5.3 Unit tests for ConstraintInjector — injection, prioritization
