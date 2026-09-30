# Design

## Context

- 方向现状:`RuntimePort`(`runtime/ports.py:42`,16 方法)是"引擎→外部能力服务",保留不动;平台→后端方向不存在。`core/composition/agent_execution_port.py` 是平台侧服务装配点,本 change 不触碰。
- 相邻规格边界:`agent-invocation`(EnginePort agent_execute,引擎内)与 `execution-state-log`(引擎内 WAL/投影)均不受影响;新契约的事件 envelope 是跨进程**归一化投影**,Pregel 原始事件(superstep 等)保留为后端详情。
- 执法先例:`tests/test_runtime/test_runtime_self_sufficiency.py` 的 AST 全量导入扫描(覆盖函数内/条件内/try 内)是纯度探针的直接模板;`tests/test_layering_domain.py` 钉跨域 import。
- 探索阶段已裁决(proposal 决策记录):schema 权威=手写文件;能力三级制直落;制品 tar.gz 起步;capability spec 拆 2 个。停车场(试点语言/CI/冻结时点)归第二个 change。

## Goals / Non-Goals

**Goals**

- 交付权威 schema 文件 + dataclass 映射 + 标准样本,三方互检由测试钉住。
- `AgentExecutionBackend` 六方法 + 三级能力声明 + 三轴归属 + Stub 负例。
- `SandboxProvider` 最小契约(独立版本)。
- 制品 manifest schema(tar.gz + sha256)。
- 纯度探针覆盖 `contracts/` 与 `execution/`。

**Non-Goals**

- 不把新接口接入 `WorkflowExecutionService`/任何现有执行路径(step5a)。
- 不实现真实后端、不做非 Python 试点(`execution-backend-nonpython-pilot`)。
- 不建控制面模型(Task/Run/Deployment ORM 归 step4)、不迁移目录、不改 `RuntimePort`。
- 不定义 Memory/Evaluation/Gateway 契约;不冻结契约版本(step8)。

## Decisions

### D1: 权威 schema 机制(探索裁决落地)

`src/hecate/contracts/schemas/` 按主题分文件:`execution-request`、`event-envelope`、`errors`、`capabilities`、`references`、`artifact-manifest`、`sandbox`。每文件头部带 `$id`(含契约名与 0.x 版本)。**没有任何生成步骤**:schema 手写、映射手写、样本手写;一致性完全由测试的三方互检承载(样本×schema 校验、映射×样本解析、映射往返)。入站运行时校验未来在网关/adapter 用 `jsonschema` 对同一批文件执行——单一校验权威。

*备选*:pydantic 导出。否决——跨语言边界上让一种实现语言当主人、导出近似语义(pydantic 自定义约束不完全翻译),且契约 diff 埋进实现 PR;详见 proposal 决策记录。

### D2: 包布局与依赖方向

```
src/hecate/contracts/          # 语言中立词汇: schemas/ + dataclass 映射
    schemas/*.json             # 权威 IDL(含 $id 与版本)
    execution/                 # ExecutionRequest/EventEnvelope/Errors/Refs 的 dataclass
src/hecate/execution/          # 平台→后端扩展点(非 runtime 域)
    backend.py                 # AgentExecutionBackend + CapabilityLevel + BackendCapabilities
    stub.py                    # StubExecutionBackend(pause 永久 unsupported)
    sandbox.py                 # SandboxProvider ABC + 状态枚举
tests/test_execution/          # 契约测试 + 三方互检 + 纯度探针
tests/scenarios/tools/../samples → 标准样本放 tests/test_execution/samples/
```

`contracts/` 与 `execution/` 互不依赖 ORM/Web 框架;`execution/` 可依赖 `contracts/`(接口参数用契约类型)。两者是新的顶层目录,分层测试同步登记前缀。

### D3: 类型形状(最小集)

- 引用:`BackendRef(kind, issuer_domain, id)` 的 dataclass;`TaskRef`/`RunRef`(平台侧)与 `SessionRef`/`TurnRef`(供应商侧)为不同 kind 枚举值,schema 层以 enum 分离——类型不混用由测试断言(构造 `RunRef(kind="session")` 非法)。
- `ExecutionRequest`:task/run 引用、部署与版本引用、授权上下文引用(不内嵌权限内容)、输入与 artifact 引用、预算/截止时间、幂等键、trace 关联、`backend_config_ns`(命名空间字典)。
- envelope:`event_id`、`task_ref/run_ref`、`source_sequence`、`correlation_id/causation_id`、`occurred_at/received_at`、`payload_schema_ref`、`evidence_ref`。
- 错误:`BackendError(code, request_ref, message, detail_ns)`;code 枚举六类。
- 能力:`BackendCapabilities` 为逐能力 `CapabilityLevel`(StrEnum:unsupported/cooperative/enforced)+ 可选 `verification`(来源、时间、失效条件);三轴归属为 `OwnershipAxes(harness, environment, tool_execution)` 三个枚举字段。

### D4: 六方法与可选能力的表达

可选能力**不是接口方法**:`AgentExecutionBackend` 只有六方法;`provide_input`/`resolve_approval`/`pause`/`resume`/`export_context` 通过能力声明协商,具体交互语义按能力走(首版只定义声明与 unsupported 错误路径;cooperative/enforced 的完整交互样本随非 Python 试点修订)。Stub 的 `pause: unsupported` 是常驻负例:契约测试断言对 Stub 调暂停路径得到结构化 UNSUPPORTED 错误。

### D5: 制品 manifest

JSON manifest + tar.gz 载体。manifest 字段:契约版本、后端类型与兼容版本、定义入口、逐文件 `{path, sha256, size}`、所需能力清单、工具 schema 与权限声明、模型/组件配置**引用**、产物 schema、证据引用;`backend_ns` 命名空间放专属图/脚本。加载侧校验函数(摘要、结构)在 `contracts/` 提供(纯函数,不碰 IO 之外的状态);"不执行安装脚本"由 schema 无此类字段 + 校验拒绝未知可执行声明承载。

### D6: SandboxProvider 独立文件独立版本

`execution/sandbox.py` 独立 ABC;sandbox schema 单独 `$id` 版本。与 `agent-environment` 规格(内置 EnvironmentManager)的关系:进程外契约 vs 进程内实现,互不修改;step7 才包装 Docker 参考实现。

### D7: 纯度探针实现

复用 runtime 自足探针模式:AST 扫描 `contracts/`+`execution/` 全部 import 位置,禁止前缀 `hecate.models`、`hecate.studio`、`hecate.channel`、`hecate.tools`、`hecate.ops`、`hecate.enterprise`、`sqlalchemy`、`fastapi`、`hecate.runtime.pregel`(runtime 包本身允许,只禁 Pregel 具体引擎模块)、`litellm`。同步在 `tests/test_layering_domain.py` 登记两个新顶层前缀,禁止其他域 import 它们的内部实现(公开契约除外——`models/` 等域未来可 import contracts)。

### D8: 扩展点合规声明

`AgentExecutionBackend`:第二实现=StubExecutionBackend(本 change),具名消费者=非 Python 试点(`execution-backend-nonpython-pilot`,已列入方案 §七首轮拆分表)与 step5a 的 `HecateExecutionBackend` 包装。`SandboxProvider`:第二实现=本 change 的 InMemory/Stub sandbox 双(测试替身),具名消费者=step7 Docker adapter 与 step8 云端试点。满足 runtime-pluggability 的两选一,不修改该规格。

## Risks / Trade-offs

- [三处同步成本随契约面积线性增长] → 最小集纪律(specs 以负例钉住"能力扩张不走接口方法");样本按"每类至少一个"控制数量,不做组合爆炸。
- [0.x 草案期的破坏性变更预期] → 版本字段 + `version_conflict` 语义先行,试点 change 显式修订并升草案版本号。
- [ Stub 与未来真实实现语义分叉 ] → 契约测试参数化设计:同一组用例对 Stub 与(未来的)真实后端运行,Stub 只是第一个被测实现。
- [ 纯度探针误伤合法依赖 ] → 允许清单显式(仅 contracts 自身与 stdlib/typing),白名单变更须改探针测试本身。

## Migration Plan

纯增量:greenfield 包 + 测试,无部署/回滚动作;revert 即删除。不触碰任何现有运行路径、API 与数据库。

## Open Questions

(无——停车场问题(试点语言、试点 CI 接入、冻结时点)属于第二个 change 的决策范围,已登记在 proposal。)
