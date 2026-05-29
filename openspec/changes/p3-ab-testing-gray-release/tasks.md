## 1. Model Routing

- [x] 1.1 Create `services/llm/routing.py` with ModelRouter class
- [x] 1.2 Implement cost-aware routing — select cheapest model
- [x] 1.3 Implement latency-aware routing — select fastest model
- [x] 1.4 Implement capability-aware routing — match model to task requirements

## 2. A/B Testing

- [x] 2.1 Create `services/llm/ab_testing.py` with ABTestManager class
- [x] 2.2 Implement traffic splitting — route by percentage
- [x] 2.3 Implement metrics collection — success rate, latency, token usage
- [x] 2.4 Implement statistical significance calculation

## 3. Gray Release

- [x] 3.1 Create `services/llm/gray_release.py` with GrayReleaseManager class
- [x] 3.2 Implement weighted routing — route by configurable weights
- [x] 3.3 Implement progressive rollout — update weights over time

## 4. Integration

- [x] 4.1 Integrate ModelRouter with LLMService — replace simple fallback
- [x] 4.2 Add routing configuration to Agent model

## 5. Testing

- [x] 5.1 Unit tests for ModelRouter — cost, latency, capability routing
- [x] 5.2 Unit tests for ABTestManager — traffic splitting, metrics
- [x] 5.3 Unit tests for GrayReleaseManager — weighted routing, rollout
