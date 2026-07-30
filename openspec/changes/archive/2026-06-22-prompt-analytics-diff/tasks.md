## 1. 模型变更与迁移

- [x] 1.1 向 `models/prompt.py` 中的 `PromptVersionModel` 添加 `commit_message: Mapped[str | None]` 字段
- [x] 1.2 更新 `PromptVersionReadSchema` 以包含 `commit_message: str | None`
- [x] 1.3 更新 `PromptUpdateSchema` 以接受 `commit_message: str | None = None`
- [x] 1.4 创建 Alembic 迁移，向 prompt_versions 表添加 commit_message 列，从当前头链入

## 2. 受保护标签

- [x] 2.1 向 `core/config.py` 添加 `PROTECTED_PROMPT_LABELS: list[str] = ["production"]` 设置
- [x] 2.2 在 `PromptService.update_prompt()` 中实现受保护标签检查：解析当前标签和新标签，如果添加或移除了受保护标签且 AuthContext.role != "admin"，则引发 ForbiddenError

## 3. 版本差异

- [x] 3.1 创建 `api/management/prompt_diff.py`，包含 `router` 和 `GET /api/prompts/{id}/diff?from_version=X&to_version=Y` 端点
- [x] 3.2 使用 `difflib.HtmlDiff` 和 `difflib.unified_diff` 实现行级差异
- [x] 3.3 使用 `format=json` 查询参数支持结构化 JSON 和原始统一差异输出格式
- [x] 3.4 差异响应中包含变量变更和 token 计数增量

## 4. 提示分析服务

- [x] 4.1 创建 `services/prompt_analytics_service.py`，包含 `PromptAnalyticsService` 类，注入 AsyncSession
- [x] 4.2 实现 `get_version_stats(prompt_id, version, start_time, end_time)`——从 TraceModel 聚合：COUNT(*)、AVG(duration_ms)、P50/P95/P99 延迟、SUM(total_tokens)、错误率
- [x] 4.3 实现 `compare_versions(prompt_id, version_a, version_b)`——返回两个版本的并排指标比较（调用次数、延迟、token、错误率、成本）

## 5. LLMWorker 追踪链接

- [x] 5.1 修改 `engine/workers/llm_worker.py`：当 `agent_config` 包含 `prompt_id` 时，将 `metadata.prompt_id` 和 `metadata.prompt_version` 写入 TraceModel metadata_
- [x] 5.2 使写入条件化——未配置提示时不写入元数据
- [x] 5.3 验证过滤 `metadata_->>'prompt_id'` 的追踪查询返回正确的调用

## 6. 版本分析

- [x] 6.1 创建 `api/management/prompt_analytics.py` 路由器
- [x] 6.2 实现 `GET /api/prompts/{id}/versions/{version}/analytics?start=&end=`——返回聚合指标：total_calls、avg_latency_ms、p50_latency、p95_latency、p99_latency、total_tokens、error_rate、estimated_cost
- [x] 6.3 实现 `GET /api/prompts/{id}/compare?version=X&version=Y&start=&end=`——返回两个版本之间的指标 deltas

## 7. AI 变更摘要

- [x] 7.1 实现 `POST /api/prompts/{id}/versions/{version}/summary`——将版本差异发送到 LLMService，返回人类可读的变更摘要
- [x] 7.2 如果摘要使用的 LLM 调用失败，优雅降级（返回 "摘要不可用" 而非错误）

## 8. 端点注册

- [x] 8.1 在 `main.py` 中将 prompt_diff 和 prompt_analytics 路由器注册到主应用中

## 9. 测试

- [x] 9.1 测试模型：创建带提交消息的 PromptVersionModel，读取提交消息，更新提交消息
- [x] 9.2 测试受保护标签：管理员可以更新生产标签，非管理员更新受保护标签时收到 403，非管理员可以修改非受保护标签
- [x] 9.3 测试差异端点：两个版本之间的行级差异，空差异（相同版本），变量差异，token 计数增量
- [x] 9.4 测试分析端点：使用模拟 TraceModel 数据聚合指标，无数据时的空结果，不同时间范围的过滤
- [x] 9.5 测试比较端点：两个版本之间的并排指标 deltas，相同版本的零 deltas
- [x] 9.6 测试 LLMWorker 追踪链接：配置了 prompt_id 时写入元数据，无 prompt_id 时不写入

## 10. 验证

- [x] 10.1 运行 `ruff check src/hecate/ tests/`——零错误
- [x] 10.2 运行 `ruff format --check src/ tests/`——零更改
- [x] 10.3 运行 `mypy src/`——零错误
- [x] 10.4 运行 `python -m pytest tests/ -q`——所有测试通过
