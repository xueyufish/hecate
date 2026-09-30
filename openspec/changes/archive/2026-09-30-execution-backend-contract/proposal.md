# Proposal

## Why

演进方案(`docs/refactor/enterprise-agent-platform-evolution-plan.md`)step3 要求建立"平台调用 Runtime"方向的语言中立契约。当前只有方向相反的 `RuntimePort`(`runtime/ports.py:42`,引擎调用外部能力服务);独立消费(业务 App 只装执行组件)与外部后端准入(自托管异构 Runtime、托管 harness)都以这层契约为前置——step5a 共享装配按它对齐,step8 用真实异构后端验证并冻结它。没有它,后续每一步都要临时发明接口语义。

本 change 是两个 change 中的第一个:交付契约主体;真实非 Python 后端的窄范围验证(`execution-backend-nonpython-pilot`)是后续独立 change,其产出是"按真实差异修订本契约"。

## What Changes

- 新增 `src/hecate/contracts/` 契约层,**手写 JSON Schema 文件为权威 IDL**,Python 纯 dataclass 仅作内置映射(不用 pydantic,杜绝再导出一份 schema 的可能;未来独立发行时依赖极轻)。标准样本三方互检:样本通过 schema 校验、Python 映射能解析样本、映射往返一致。入站运行时校验在网关/adapter 层用 `jsonschema` 对同一批权威文件执行(单一校验权威;`jsonschema` 已是基础依赖)。
- 新增 `src/hecate/execution/backend.py`:`AgentExecutionBackend` 最小六方法(`describe_capabilities` / `submit` / `get_run` / `read_events` / `list_artifacts` / `request_cancel`);可选能力(`provide_input` / `resolve_approval` / `pause` / `resume` / `export_context`)以 `unsupported / cooperative / enforced` 三级声明,调用 unsupported 能力返回结构化错误而非伪造成功。能力模型按 harness 所有方 × 环境所有方 × 工具执行点三轴登记,附验证来源与失效条件;供应商 session/turn 引用与平台 Task/Run 引用在类型上不混用;供应商配置走命名空间,核心只验证通用字段。
- 契约核心类型:`ExecutionRequest`(任务/运行 ID、部署与版本引用、授权上下文引用、输入与 artifact 引用、预算/截止时间、幂等键、trace 关联)、事件 envelope(event_id、source_sequence、游标、causation/correlation、payload schema 引用、证据引用)、错误语义六类(unsupported / authorization_denied / budget_exhausted / version_conflict / unreachable / outcome_unknown,超时不等于失败)、跨语言编码规则(ID/时间/枚举/可空字段表示、未知字段容忍、版本协商、幂等键与 trace 关联)。标识为带签发域的逻辑引用,不要求接收方查询平台 ORM;独立宿主从可信本地登记分配标识,受管 adapter 负责映射。
- 最小执行制品 manifest:首版载体为**标准归档(tar.gz + sha256 摘要清单)**,不自创专用包格式、不自动执行清单中的安装脚本;后端专属图/脚本放命名空间(其他 Runtime 无须实现 Pregel DSL);不含明文凭据、业务数据或在线平台 ID 查找前提。step5 本地加载器消费该格式,step11 沿用其发布语义。
- `SandboxProvider` 最小契约(能力发现、创建/查询环境、提交/查询命令、文件传输、终止、续租):环境与命令分别使用幂等 ID,命令超时结果为 unknown/待对账;创建中/就绪/终止中/已终止/失败/状态未知的状态映射;暂停/恢复等可选状态按能力协商。以**独立 capability spec** 承载(与执行契约分别版本化)。现有 `agent-environment` 规格(内置 EnvironmentManager)不受影响——本契约是进程外边界,Docker 参考实现的包装发生在 step7。
- `StubExecutionBackend` + 参数化契约测试 + 导入纯度测试(`contracts/` 与 `execution/` 不得 import SQLAlchemy、FastAPI、Pregel 具体类或供应商 SDK,复用 runtime 自足探针的 AST 扫描模式)。Stub 永久声明 `pause=unsupported` 作为"不支持的能力必须显式报错"的常驻负例。
- 契约版本标记 **0.x 草案、未冻结**:冻结推迟到 step8 非 Python 试点与真实后端验证之后("不能仅用 Stub 冻结接口");执行契约与 Sandbox 契约、未来的 Memory/Evaluation 契约分别独立版本,不设全平台同步版本号。
- 不修改现有执行路径:`RuntimePort` 保留不动,`WorkflowExecutionService` 不接入新接口(接入属 step5a),不建控制面、不迁目录。

## Capabilities

### New Capabilities

- `execution-backend-contract`:平台调用执行后端的最小契约——六方法接口、三级能力声明与三轴归属、逻辑引用与事件 envelope、错误语义与跨语言编码规则、标准样本与权威 schema 的三方互检、执行制品 manifest 与本地加载契约(含最小集纪律:能力扩张走能力声明协商而非接口方法增殖)。
- `sandbox-provider-contract`:进程外 Sandbox 提供方的最小契约——环境/命令幂等 ID、状态映射、租约与终止、命令结果未知的显式表达;与执行契约分别版本化。

### Modified Capabilities

(无——`runtime-pluggability` 的"新扩展点需第二实现或具名消费者"规则在本 change 由 `StubExecutionBackend`(第二实现)+ 非 Python 试点(具名消费者,后续 change)共同满足,只需在 design 中声明,不修改该规格;`agent-environment`、`RuntimePort` 相关规格均不受影响。)

## Impact

- **新增**:`src/hecate/contracts/`(schema 文件 + dataclass 映射)、`src/hecate/execution/`(backend.py、sandbox.py)、`tests/test_execution/`(契约测试、样本校验、纯度探针)、标准样本文件。
- **修改**:分层测试扩展覆盖新顶层包(imports 纯度);无产品源码行为变更,无数据库 schema 变更,无现有 API 变更。
- **依赖**:零新增运行时依赖(`jsonschema` 已在基础依赖)。
- **CI**:纯 Python 增量,随既有 pytest/ruff/mypy 门运行;非 Python 试点不在本 change。
- **决策记录**(探索阶段裁决,design 将展开):schema 权威源=手写文件(分叉 1);能力三级制按方案直落(分叉 2);制品首版 tar.gz 起步、OCI 留待 step11 按需(分叉 3);capability spec 拆 2 个(分叉 4)。**停车场**(第二个 change 启动前定):非 Python 试点语言(倾向 TypeScript)、试点是否进 CI(倾向先手动运行+记录证据)。
