## Why — 动机

引擎层目前没有机制在 superstep 周期内拦截或控制单独的 LLM 调用和工具执行。安全扫描存在于 API 边界（`SecurityMiddleware`），但在执行期间从未被调用，并且无法在不修改核心服务代码的情况下将自定义的前置/后置检查（安全、合规、成本限制）注入到代理运行时。引擎级别的 guardrail hook 接口使得可插拔的安全、合规和可观测性策略能够每步执行——这是 P3 AI 驱动的 guardrails（9.1a/9.1b）和多代理编排的前提条件。

## What Changes — 变更内容

- 在 `engine/guardrail.py` 中添加 4 个 hook ABC：`PreLLMHook`、`PostLLMHook`、`PreToolHook`、`PostToolHook`
- 为每个 hook 添加 `NoOp` 实现（默认——透传）
- 将一个 `GuardrailRegistry` 类添加到 `engine/guardrail.py`，持有 hook 列表并在引擎执行点调用它们
- 在 `LLMWorker` 中集成 hook 调用点：LLM 调用之前/之后，工具执行之前/之后
- 不中断现有的 worker 行为——hook 默认注册时不执行任何操作

## Capabilities — 能力变更

### 新增能力
- `guardrail-hook`: 用于 LLM/工具调用的可插拔前置/后置拦截

### 修改的能力
- 无

## Impact — 影响范围

- **新文件**: `src/hecate/engine/guardrail.py`（4 个 Hook ABC + NoOp 实现 + GuardrailRegistry）
- **修改的文件**: `src/hecate/engine/worker.py` 中的 LLMWorker（添加 4 个调用点）
- **新测试**: `tests/test_engine/test_guardrail.py`
- **无破坏性变更**: 默认的 NoOp hook 保留现有行为
- **无新依赖**: 仅使用 stdlib