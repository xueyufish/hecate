# Proposal

## Why

演进方案 step5e 与 ADR-037 已把"共享执行应用服务解除对 `PregelRuntime` 的唯一绑定"登记为实现现状的待解除项：`packages/hecate-runtime/src/hecate_runtime/execution_service.py` 在模块级导入 `PregelRuntime` 并以 `runtime: PregelRuntime` 声明请求字段，平台 `HecateExecutionBackend`（`src/hecate/execution/builtin.py`）与 Runner `ExecutionEngine`（`packages/hecate-runner/src/hecate_runner/engine.py`）都直接向它传入 Pregel 实例。`runtime-pluggability` 规范要求扩展点须有第二实现或具名消费者；共享服务当前对 Pregel 的具体类依赖使后续 Controller（不构造 `GraphConfig`/`PregelRuntime`）无法经同一服务执行，是 Controller 实施前的第一处代码边界。

本 change 只解除这一具体类绑定：共享执行服务改为面向最小 Runtime 内部执行驱动契约工作，Pregel 经适配实现该契约，两个真实消费者迁移到驱动边界。不改变任何执行、事件、审批等待或恢复语义。

## What Changes

1. **新增 Runtime 内部执行驱动契约 `ExecutionDriver`**（`hecate_runtime` 内，普通名词 + `abc.ABC`，按仓库命名规则不用 `Port`/`Base` 后缀）：只覆盖共享执行服务实际消费的执行面——`execute(session_id, initial_input, execution_mode, stream_mode, resume_value) -> AsyncIterator[event]`（流可 `aclose()`）；`resume_value` 在契约层是不透明恢复负载，由具体驱动自行解释（Pregel 驱动解释为 channel 恢复状态），不把图语义泄漏进契约。契约服务于内置 Controller 与 Workflow/Pregel 驱动，不扩成外部 Runtime 插件框架。
2. **Pregel 驱动实现**：新增 `PregelExecutionDriver` 包装组合好的 `PregelRuntime` 并转发全部参数与事件；Pregel 类本身不加接口、不修改。现有执行、事件捕获、协作取消、提前关闭（`aclose`）与结果分类语义逐字保留在共享服务中。
3. **共享执行服务解绑**：`RuntimeExecutionRequest.runtime` 改为 `ExecutionDriver`；`execution_service.py` 不再导入或判断 `PregelRuntime`；继续独占事件观察（observer）、协作取消检查、`STOP_UNKNOWN` 提前 `aclose`、`asyncio.CancelledError` → 调度取消包装与成功/失败/取消/未知四类结果分类。
4. **迁移两个真实消费者**：`HecateExecutionBackend` 与 Runner `ExecutionEngine` 在组装点把 `PregelRuntime` 包进驱动（组装产物 `ExecutionAssembly.runtime` 类型同步更新），二者从各自调用点经驱动边界传入；不允许只改类型注解、加空接口或让测试替身绕过——迁移以真实 Pregel 驱动跑通既有回归为证。
5. **验证补齐**：自足探针式测试阻断 `hecate_runtime.pregel` 导入后，`execution_service` 仍可加载并用非图 Stub 驱动完成全部结果分类；真实 Pregel 驱动下 builtin 与 Runner 的事件、审批等待（`waiting_approval`）、原生恢复与取消回归通过。根 `testpaths` 只含 `tests/`，本 change 的验证显式包含 `packages/hecate-runtime/tests/` 与 Runner 测试。

**边界**：不实现 Controller 循环；不新增决策账本、存储或持久化语义；不迁移聊天路由；不翻默认开关；不授予新的恢复保证；`resume_value` 的 Pregel 解释与 step6d 现状一致。契约不面向外部 Runtime 接入（该范围已按 ADR-037 移出方案）。

## Capabilities

### New Capabilities

（无——执行驱动是 Runtime 内部接缝，不新增面向外部的行为能力；规范变更登记在既有 `runtime-pluggability` 能力下。）

### Modified Capabilities

- `runtime-pluggability`: 新增"共享执行驱动可替换"要求——`execution_service` 面向 Runtime 内部执行驱动契约工作且不导入具体图执行类；驱动契约有生产实现（Pregel）与契约替身（Stub），由同一组契约测试参数化验证；两个具名消费者（builtin 后端、Runner 引擎）经驱动边界执行；新增扩展点登记其第二实现、消费者与"不扩成外部 Runtime 插件框架"的边界。

## Impact

- **代码**：`packages/hecate-runtime/src/hecate_runtime/execution_service.py`（解绑 + 契约定义或同包新文件）、`execution_assembly.py`（组装产物类型）、`src/hecate/execution/builtin.py` 与 `packages/hecate-runner/src/hecate_runner/engine.py`（组装点包装）；无数据库、无 API 契约、无配置变更。
- **测试**：`packages/hecate-runtime/tests/test_execution_service.py`（契约替身驱动 + 阻断 Pregel 导入的环境测试）；Runner 与 builtin 既有回归显式纳入验证。
- **规范**：`openspec/specs/runtime-pluggability/spec.md`（经本 change delta 在归档时同步）。
- **不涉及**：`pregel.py`、聊天路由/开关、文档定位文本（ADR-037 已登记该现状，完成后按收尾惯例在后续 change 的复核中引用即可）。
