## 1. Config Settings — 配置设置

- [x] 1.1 在 `src/hecate/core/config.py` 的 `Settings` 类中添加 `CONTEXT_OFFLOAD_ENABLED: bool = True`
- [x] 1.2 在 `src/hecate/core/config.py` 的 `Settings` 类中添加 `CONTEXT_OFFLOAD_THRESHOLD_TOKENS: int = 6000`
- [x] 1.3 为两个新设置添加 `.env.example` 条目并附注释

## 2. ContextOffloader Implementation — ContextOffloader 实现

- [x] 2.1 创建 `src/hecate/services/context/offloader.py`，包含 `ContextOffloader` 类骨架（导入、接受可选 `AgentEnvironment` 的 `__init__`）
- [x] 2.2 实现 `offload(messages, session_id) -> dict` 方法：将消息序列化为 JSON，写入 `memory/sessions/{session_id}/offloaded_{timestamp}.json`，返回引用桩
- [x] 2.3 实现 `_build_stub(path, messages) -> dict` 方法：生成紧凑的 system 角色引用消息，包含主题摘要和 `read_file` 提示，限制在 500 字符
- [x] 2.4 实现 `_filename_timestamp() -> str`：生成 `YYYYMMDDHHMMSS` 格式；通过 `environment.exists()` 处理同一秒内的冲突，使用 `_1`、`_2` 后缀
- [x] 2.5 实现 `_heuristic_summary(messages) -> str`：提取每条用户消息的前 200 个字符，连接并截断到总计 500 字符
- [x] 2.6 添加 `is_enabled() -> bool` 方法：当 environment 为 None 时返回 False（指示管道跳过）
- [x] 2.7 更新 `src/hecate/services/context/__init__.py` 以导出 `ContextOffloader`

## 3. LLMWorker Pipeline Modification — LLMWorker 管道修改

- [x] 3.1 在 `LLMWorker._apply_context_pipeline()`（engine/workers/llm_worker.py）中，读取 `execution_context.get("context_offloader")` 和 `settings.CONTEXT_OFFLOAD_ENABLED`
- [x] 3.2 捕获丢弃的消息：在 `select_messages` 返回后计算 `dropped = messages[len(selected):]`（选择窗口之前的消息）
- [x] 3.3 通过 `ctx_engine.estimate_tokens(dropped)` 计算丢弃的 token 数；检查是否达到 `CONTEXT_OFFLOAD_THRESHOLD_TOKENS`
- [x] 3.4 如果达到阈值且卸载器已启用：调用 `offloader.offload(dropped, session_id)`，接收 stub
- [x] 3.5 重建过滤后的列表为 `[stub] + selected`，重新估算 tokens
- [x] 3.6 如果仍然超出预算：继续进行 `ctx_engine.compress([stub] + selected)` 作为最后手段
- [x] 3.7 如果卸载器不存在或未达到阈值：继续执行 `compress(selected)` 和之前一样（向后兼容）
- [x] 3.8 将相同的更改应用于 `execute_stream()` 管道路径

## 4. Execution Context Wiring — 执行上下文接线

- [x] 4.1 在 PregelRuntime（engine/pregel.py）中，向 `__init__` 添加 `context_offloader` 参数
- [x] 4.2 在 `_execution_context()` 中，当 `self._context_offloader` 不为 None 时注入 `ctx["context_offloader"] = self._context_offloader`
- [x] 4.3 更新 WorkflowExecutionService（或构建 PregelRuntime 的地方），当 `AgentEnvironment` 可用时构造 `ContextOffloader(environment=env)` 并注入到 PregelRuntime

## 5. Tests — 测试

- [x] 5.1 创建 `tests/test_services/test_context/test_offloader.py`，包含 `ContextOffloader` 单元测试
- [x] 5.2 测试：卸载将有效的 JSON 文件写入环境，完整消息结构保留
- [x] 5.3 测试：卸载返回紧凑的 stub，role=system，content ≤ 500 字符，包含文件路径
- [x] 5.4 测试：stub 包含 `read_file("path")` 检索指令
- [x] 5.5 测试：stub 主题摘要提取用户消息前缀，截断到 500 字符
- [x] 5.6 测试：文件名时间戳格式为 `YYYYMMDDHHMMSS`，同一秒冲突得到 `_1` 后缀
- [x] 5.7 测试：`is_enabled()` 在 environment 为 None 时返回 False
- [x] 5.8 创建/扩展 `tests/test_engine/test_workers/test_llm_worker_pipeline.py`，包含管道集成测试
- [x] 5.9 测试：卸载器存在且达到阈值时管道执行卸载（文件写入，stub 在过滤后的列表中）
- [x] 5.10 测试：卸载器不存在时管道跳过卸载（向后兼容 — 匹配旧的 4 步输出）
- [x] 5.11 测试：丢弃的 tokens 低于阈值时管道跳过卸载（继续进行压缩）
- [x] 5.12 测试：卸载不足时管道回退到压缩（stub + selected 仍然超出预算）
- [x] 5.13 测试：通过配置标志禁用管道卸载（CONTEXT_OFFLOAD_ENABLED=false → 跳过卸载）

## 6. Documentation — 文档

- [x] 6.1 为 `ContextOffloader` 类和所有公共方法添加文档字符串（英文，根据 AGENTS.md）
- [x] 6.2 更新 `src/hecate/services/context/__init__.py` 模块文档字符串以提及卸载功能
- [x] 6.3 在 `_apply_context_pipeline()` 上添加内联注释，解释 5 步流程以及为什么卸载在压缩之前

## 7. Verification — 验证

- [x] 7.1 运行 `ruff check src/hecate/ tests/` — 预期 0 错误
- [x] 7.2 运行 `ruff format --check src/ tests/` — 预期全部格式化
- [x] 7.3 运行 `mypy src/` — 预期 0 错误
- [x] 7.4 运行 `python -m pytest tests/test_services/test_context/test_offloader.py -v` — 全部通过
- [x] 7.5 运行 `python -m pytest tests/test_engine/test_workers/test_llm_worker_pipeline.py -v` — 全部通过（或现有的测试文件如果命名不同）
- [x] 7.6 运行 `python -m pytest tests/ -q` — 无回归（engine + context + worker 测试足够；如果时间允许则全量运行）
