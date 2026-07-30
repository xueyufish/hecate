## 1. 结果校验

- [x] 1.1 创建 `services/validation/result_validator.py` 及 ResultValidator 类
- [x] 1.2 实现 `validate(output, schema)`——JSON Schema 校验
- [x] 1.3 实现 `validate_with_rules(output, rules)`——自定义规则校验
- [ ] 1.4 集成到 ConversationService——在注入前校验工具结果

## 2. 重试策略

- [x] 2.1 创建 `services/validation/retry_policy.py` 及 RetryPolicy 类
- [x] 2.2 实现 `ExponentialBackoffPolicy`——可配置 base, max, multiplier
- [x] 2.3 实现 `ErrorClassifier`——将错误分类为可重试/不可重试
- [x] 2.4 实现 `CircuitBreaker`——打开/半开/关闭状态
- [ ] 2.5 集成到 ConversationService——在失败时重试工具调用

## 3. 输出 Schema 校验

- [x] 3.1 创建 `services/validation/output_validator.py` 及 OutputSchemaValidator 类
- [x] 3.2 实现 `validate(output, schema)`——LLM 输出校验
- [x] 3.3 实现 `auto_repair(output)`——修复常见格式错误
- [ ] 3.4 集成到 ConversationService——校验 LLM 响应

## 4. 测试

- [x] 4.1 ResultValidator 单元测试——有效、无效、自定义规则
- [x] 4.2 RetryPolicy 单元测试——指数退避、错误分类、熔断器
- [x] 4.3 OutputSchemaValidator 单元测试——有效、无效、自动修复
- [ ] 4.4 与 ConversationService 的集成测试
