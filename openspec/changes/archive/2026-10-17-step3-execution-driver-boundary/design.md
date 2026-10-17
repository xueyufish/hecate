# Design

## Context

`packages/hecate-runtime/src/hecate_runtime/execution_service.py` 是平台与 Runner 共同消费的执行驱动服务：模块级 `from hecate_runtime.pregel import PregelRuntime`，请求字段 `runtime: PregelRuntime`。两个真实消费者——`src/hecate/execution/builtin.py` 的 `HecateExecutionBackend`（经 `ExecutionAssembly.runtime`）与 `packages/hecate-runner/src/hecate_runner/engine.py` 的 `ExecutionEngine._assemble_runtime`（本地构造 `PregelRuntime`）——都直接传 Pregel 实例。ADR-037 已将该绑定登记为"待解除的实现现状"。

关键事实：

- 服务实际消费面只有 `runtime.execute(session_id=..., initial_input=..., stream_mode=..., execution_mode=..., resume_value=...)` 产生的异步事件流，以及提前退出时对流 `aclose()`。
- 现有测试替身 `_Runtime`（`packages/hecate-runtime/tests/test_execution_service.py`）本就是鸭子类型——dataclass 注解无运行时强制力，只改注解测试依旧全绿，因此解绑必须由"阻断 Pregel 导入的环境测试"钉住。
- 自足探针（`tests/test_runtime/test_runtime_self_sufficiency.py`）已有 `sys.modules` 阻断机制（`hecate_runtime.pregel` 在阻断清单中）。
- 根 `pyproject.toml` `testpaths = ["tests"]`，包内测试默认不被根目录收集。

## Goals / Non-Goals

**Goals:**

- 共享执行服务在禁止 `hecate_runtime.pregel` 导入的环境中可加载、可用替身驱动。
- 新增最小 `ExecutionDriver` 契约 + `PregelExecutionDriver` 生产实现；两个消费者经边界迁移。
- 既有行为零变化：结果分类、取消、`aclose` 清理、审批等待、原生恢复回归通过。

**Non-Goals:**

- 不实现 Controller 循环；不新增决策账本/存储；不改 `resume_value` 的 Pregel 解释（step6d 现状）；不迁移聊天路由；不翻开关；不授予新恢复保证；契约不面向外部 Runtime。

## Decisions

### D1: 契约定义与放置

`ExecutionDriver`（`abc.ABC`，单一抽象方法 `execute(**params) -> AsyncIterator`）与 `PregelExecutionDriver` 放在 `hecate_runtime` 包内新文件 `execution_driver.py`，与 `execution_service.py` 同包平级。理由：契约属于 Runtime 内核（服务与驱动共享），不该放在 `ports.py`——那是 Runtime↔领域的六边形接缝（`XxxPort` 保留命名），而这是 Runtime 内部组件接缝，按命名规则用普通名词 + `abc.ABC`。

参数契约显式列出五个具名参数（`session_id`、`initial_input`、`execution_mode`、`stream_mode`、`resume_value`），而非 `**kwargs`——让契约面可审、让 Stub 实现显式接收；`resume_value: Any` 注释为不透明恢复负载。

### D2: Pregel 实现为组合包装，Pregel 类零修改

`PregelExecutionDriver(PregelRuntime)` 组合（构造持有实例，`execute` 转发并返回其异步流）优于继承：继承会把 `PregelRuntime` 的全部公共面带进驱动的类型，扩大契约表面积；组合让"驱动=可执行面"显式。`pregel.py` 不加基类、不改一行。

### D3: 驱动在组装点构造一次，共享服务消费者取用 driver 字段

实施中发现 `AssembledExecution.runtime` 有三类消费者：builtin 后端（传共享执行服务）、studio 聊天执行服务（直接驱动 Pregel——`runtime.execute` 及 Pregel 特有的 `snapshot_at_version`）、以及 Runner 自行构造不经过它。因此不能把 `runtime` 字段改为 `ExecutionDriver`（会破坏聊天路径），也不能把 `snapshot_at_version` 提进契约（图私有语义泄漏，违反规范 delta）。修正：`AssembledExecution` 保留 `runtime: PregelRuntime`，**新增 `driver: ExecutionDriver` 字段**，`assemble_execution` 构造时用 `PregelExecutionDriver` 包装一次；builtin 调用点改为传 `assembled.driver`（mypy 拒绝再传裸 Pregel），聊天路径零改动。将来 Controller 接入时由其组装产物提供自己的 driver。

### D4: 用三类测试钉住解绑与行为不变

1. **环境测试**（新）：`sys.modules` 阻断 `hecate_runtime.pregel`（及 `hecate.runtime.pregel` shim 名）后导入 `execution_service`，用非图 Stub 驱动跑完整事件流——成功/异常/STOP_UNKNOWN/协作取消/调度取消五分类断言。探针同款机制有既有先例。
2. **契约参数化测试**（新）：同一组用例（事件逐个转发、`aclose` 传播、`resume_value` 原样透传）分别跑 `PregelExecutionDriver`（真实 Pregel）与 Stub 驱动。
3. **既有回归显式收集**：`packages/hecate-runtime/tests/test_execution_service.py` 现有断言保持通过（替身类型改为显式 `ExecutionDriver` 子类，行为不变）；Runner 持久任务/审批/恢复相关测试与 `src/hecate/execution` 的 builtin 回归纳入验证命令。验证命令显式列出包测试路径，规避根 `testpaths` 只含 `tests/` 的收集盲区。

### D5: 分层 spec-driven

Runtime 内部架构接缝 + 扩展点新增，按 AGENTS.md 风险规则走完整流程；delta 打在 `runtime-pluggability`（新扩展点登记第二实现与具名消费者的既有要求所在能力）。

## Risks / Trade-offs

- [抽象层转发丢失流语义（如 `aclose` 未传播导致后台任务遗留）] → `PregelExecutionDriver.execute` 仅 `return self._runtime.execute(...)`（原样返回异步生成器，不二次包装），契约测试显式断言关闭传播；提前停止回归复用现有 `aclose` 用例。
- [契约面被后续需求逐渐撑大] → delta 明文限定"服务实际消费面"与"不扩成外部 Runtime 插件框架"；新增契约参数须按 runtime-pluggability 既有规则重新过第二实现/消费者论证。
- [Stub 驱动让测试绿但消费者仍偷传 Pregel] → 消费者迁移以组装点类型变更为准（`ExecutionAssembly.runtime: ExecutionDriver`、Runner `_assemble_runtime` 返回驱动），mypy 拒绝裸 `PregelRuntime` 传入；回归以真实 Pregel 驱动跑通为准，非替身。
- [包内测试不被默认收集造成假绿] → tasks 的验证命令显式列出 `packages/hecate-runtime/tests/...` 与 Runner 测试目录，并要求报告实际收集数。

## Migration Plan

单 commit 可回滚的纯重构：新增契约与驱动 → 组装点类型切换 + 服务解绑（同一 change 内原子完成，不留中间态）→ 测试补齐。回退即 revert 该 commit；无数据、无配置、无路由影响。

## Open Questions

（无——契约面、放置、包装点、测试策略均已定；Controller 何时接入属后续 change。）
