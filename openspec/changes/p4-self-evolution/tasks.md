## 1. Trajectory Analysis

- [ ] 1.1 Create `services/evolution/trajectory_analyzer.py` with TrajectoryAnalyzer class
- [ ] 1.2 Implement analyze_success(trajectory) — extract success patterns
- [ ] 1.3 Implement analyze_failure(trajectory) — extract improvement points

## 2. Policy Evolution

- [ ] 2.1 Create `services/evolution/policy_evolver.py` with PolicyEvolver class
- [ ] 2.2 Implement adjust_tool_strategy(analysis) — adjust tool selection
- [ ] 2.3 Implement adjust_prompt_strategy(analysis) — suggest prompt improvements

## 3. Synthetic Environment

- [ ] 3.1 Create `services/evolution/environment_generator.py` with SyntheticEnvironmentGenerator
- [ ] 3.2 Implement generate_environment(capability) — generate training environment
- [ ] 3.3 Implement validate_difficulty(environment) — validate 20-60% success rate

## 4. Integration

- [ ] 4.1 Integrate with EvidenceTracker — trigger analysis on completion
- [ ] 4.2 Integrate with Harness optimization — use evolution insights

## 5. Testing

- [ ] 5.1 Unit tests for TrajectoryAnalyzer — success, failure analysis
- [ ] 5.2 Unit tests for PolicyEvolver — tool, prompt adjustments
- [ ] 5.3 Unit tests for SyntheticEnvironmentGenerator — generation, validation
