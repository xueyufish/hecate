## 1. Result Validation

- [ ] 1.1 Create `services/validation/result_validator.py` with ResultValidator class
- [ ] 1.2 Implement `validate(output, schema)` — JSON Schema validation
- [ ] 1.3 Implement `validate_with_rules(output, rules)` — custom rule validation
- [ ] 1.4 Integrate with ConversationService — validate tool results before injection

## 2. Retry Policy

- [ ] 2.1 Create `services/validation/retry_policy.py` with RetryPolicy class
- [ ] 2.2 Implement `ExponentialBackoffPolicy` — configurable base, max, multiplier
- [ ] 2.3 Implement `ErrorClassifier` — classify errors as retryable/non-retryable
- [ ] 2.4 Implement `CircuitBreaker` — open/half-open/closed states
- [ ] 2.5 Integrate with ConversationService — retry tool calls on failure

## 3. Output Schema Validation

- [ ] 3.1 Create `services/validation/output_validator.py` with OutputSchemaValidator class
- [ ] 3.2 Implement `validate(output, schema)` — LLM output validation
- [ ] 3.3 Implement `auto_repair(output)` — fix common format errors
- [ ] 3.4 Integrate with ConversationService — validate LLM responses

## 4. Testing

- [ ] 4.1 Unit tests for ResultValidator — valid, invalid, custom rules
- [ ] 4.2 Unit tests for RetryPolicy — exponential backoff, error classification, circuit breaker
- [ ] 4.3 Unit tests for OutputSchemaValidator — valid, invalid, auto-repair
- [ ] 4.4 Integration tests with ConversationService
