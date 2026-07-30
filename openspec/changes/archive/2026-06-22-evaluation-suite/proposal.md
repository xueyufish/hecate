## Why — 为什么

平台目前有 9 个评估器（4 个 RAG + 5 个 Agent）和基本的数据集/运行/分数基础设施。为了提供企业级评估能力，我们需要扩展到 40+ 个内置评估器，涵盖结果质量、过程正确性、交互连贯性和安全——并添加带有数据集版本管理、基于断言的通过/失败、运行比较和 CI/CD 集成的回归测试基础设施。这完成了功能 7.2a 和 7.6，使团队能够在部署前检测质量回归，并持续衡量跨版本的 Agent 性能。

## What Changes — 变更内容

- 将评估器库从 9 个扩展到 41 个，覆盖四个类别：结果层（输出质量）、过程层（工具/推理）、交互层（多轮）和通用/编程层（确定性、LLM-judge、代码执行、安全）
- 将评估器注册表从内联字典重构为结构化包，使用自动注册装饰器和基于类别的文件组织
- 使用 `JudgePromptTemplate` 模式（评分量表、系统提示、用户提示模板、输出格式）标准化 LLM-as-Judge 提示模板
- 使用可选字段扩展 `EvalInput`：`conversation_history`、`system_prompt`、`agent_id`、`session_id`，用于多轮和过程评估器
- 添加数据集版本管理：`version` 标签、用于回归比较的 `baseline_run_id`、用于冻结黄金数据集的 `is_locked`
- 添加逐项断言模型：`EvaluationItemModel` 上的 `assertions` JSON 数组（类型 + 阈值），带有数据集级别默认阈值后备
- 添加运行比较 API：POST `/api/evaluation/runs/compare` 返回每指标增量和回归标记（分数下降 > 可配置阈值）
- 添加回归触发 API：POST `/api/evaluation/regression/run` 用于 CI/CD 集成（返回通过/失败 + 报告）
- 添加评估器列表 API：GET `/api/evaluation/evaluators` 返回所有已注册评估器，包含类别、描述和必需输入字段
- 将评估回归与 8.6 告警系统集成：当运行分数低于基线时触发 `evaluation_regression` 告警类型
- 评估 LLM 调用使用单独的成本跟踪标记（`purpose=evaluation`）以避免消耗用户配额

## Capabilities — 能力

### New Capabilities — 新增能力
- `builtin-evaluators`：32 个新的内置评估器，跨越四个层（结果、过程、交互、通用/编程），带有标准化 LLM-as-Judge 提示模板和自动注册注册表
- `regression-testing`：数据集版本管理、带阈值的逐项断言模型、带回归检测的运行比较，以及用于自动化质量门控的 CI/CD 集成 API

### Modified Capabilities — 修改的能力
- `evaluation-framework`：EvalInput 扩展（conversation_history、system_prompt、agent_id、session_id）、注册表重构为基于类别的包带装饰器注册、确定性评估器并行执行优化
- `evaluation-dataset`：数据集版本管理字段（version、baseline_run_id、is_locked）、项断言 JSON 字段、项标签用于分组
- `evaluation-api`：用于评估器列表、运行比较、回归触发和数据集版本管理的新端点
- `alerting`：新的 `evaluation_regression` 告警类型，用于分数回归检测

## Impact — 影响

- **新文件**：`services/evaluation/evaluators/` 包（format.py、content.py、citation.py、tool.py、multi_turn.py、judge.py、safety.py、programmatic.py）、`services/regression_service.py`、`services/evaluation/prompt_templates.py`
- **修改的模型**：`models/evaluation.py`（DatasetModel 上的 version、baseline_run_id、is_locked；ItemModel 上的 assertions、tags）、`models/alert.py`（新的 AlertType）
- **修改的服务**：`services/evaluation/evaluator.py`（注册表重构）、`services/evaluation/types.py`（EvalInput 扩展）、`services/evaluation/engine.py`（并行确定性执行）
- **修改的 API**：`api/evaluation.py`（新端点）、`api/management/alerts.py`（新的信号提供者）
- **迁移**：新的 Alembic 迁移用于数据集版本管理 + 项断言列
- **依赖**：无新的外部包——所有评估器使用现有的 LLMService 或确定性逻辑
- **测试**：每评估器单元测试 + 回归比较测试 + CI/CD API 测试
