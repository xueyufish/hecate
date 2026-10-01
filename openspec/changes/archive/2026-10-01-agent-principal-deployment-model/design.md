# Design

## Context

- step3 交付物是本设计的直接前置:`contracts/execution/references.py` 的 `BackendRef`(issuer_domain + opaque id,含 `deployment_ref` 构造器)、`capabilities.py` 的 `OwnershipAxes`(harness/environment/tool 执行点三轴)与 `CapabilityVerification`(来源/时间/失效条件)、`security.py` 的 `SecurityClaims`(`sub`=工作负载身份、`delegation_ref`)。
- `src/hecate/execution/` 域已存在(backend.py 的 `AgentExecutionBackend` ABC、sandbox.py、stub.py),且 `tests/test_layering_domain.py:66-72` 已将其登记为受保护域并要求契约纯净性(`tests/test_execution/test_contract_purity.py`)。
- 现有身份实体:`models/user.py::UserModel`、`models/organization.py::OrganizationModel`、`models/workspace.py::WorkspaceModel`。`AgentModel`(models/agent.py:23)有 workspace_id(:58)与 persona(:41),无 owner/组织字段;`AgentVersionModel`(agent_version.py:34)是版本表,本 change 复用不重建。
- 表归属规则(方案 §三):ORM 可暂放共享 `models/`,每张表一个领域负责读写。Agent/Deployment 登记属 Control Plane 职责(方案 §六能力域表),本 change 两张新表的读写权都归 execution 域;enterprise 保持认证/组织信任职责不动。

## Goals / Non-Goals

**Goals**

- 两张新表 + execution 域登记服务 + 身份链类型 + builtin 回填 + 三段式迁移,满足 spec 的 5 条 requirement。
- 旧行为零变更:回填只加登记,不触碰执行路径。
- 为 `task-run-model` 留好外键与身份链消费点。

**Non-Goals**

- 不建 Task/Run 表、不做 conversation/session 映射(step4 第二支)。
- 不新增 HTTP 路由与管理 UI(随 task-run-model/step6)。
- 不实现受管注册流程的运行时验证(宿主身份校验、断连重连——`managed-runner-enrollment`)。
- 不迁移 AgentModel.persona 字段(保留,仅降治理语义);不删除任何现有字段。

## Decisions

### D1: 两表放共享 models/,读写权归 execution 域,服务放 `execution/`

`models/agent_principal.py`、`models/agent_deployment.py` 遵循 XxxModel 惯例与 `models/base.py` 基类;应用服务 `src/hecate/execution/principal_registry.py` 与 `deployment_registry.py` 作为唯一写入方。分层测试补两条规则:execution 之外不得 import 这两张表的 Model 类做写操作(以 import 面扫描实现,与 W1-W3 同法);principal 不放 enterprise——它是 Control Plane 登记实体而非认证机制,认证仍走 `AuthContext`/`SecurityClaims`。

*备选*:principal 归 enterprise、deployment 归 execution 分两域。否决——principal 的消费方(Deployment/Run 身份链)全在 Control Plane,跨域外键徒增耦合;enterprise/ 只保留"人类与组织信任"。

### D2: 身份链类型放 `contracts/execution/`,命名 `IdentityChain`,不新造 port

frozen dataclass:发起者(人类 principal 引用)、agent principal 引用、工作负载身份(deployment 引用 + workload 标识,对齐 `SecurityClaims.sub` 语义)、可选 on-behalf-of 委派(对齐 `delegation_ref`)。语言中立、不依赖 ORM;`AgentDeploymentModel` 存 workload 身份摘要(签发域+标识),Run 侧固化完整链由第二支消费。不建 ABC/Protocol——这是数据契约,无第二实现需求(符合 runtime-pluggability 规则)。

### D3: Deployment 与契约类型的映射由 registry 方法承担,不在 ORM 里存枚举对象

表存原始列(backend_type/版本字符串、axes 的三个枚举字符串、快照 JSON、issuer_domain 唯一约束);`deployment_registry.py` 提供 `to_backend_ref()`(→ `deployment_ref(issuer_domain, id)`)与 `to_axes()`(→ `OwnershipAxes.from_dict`)转换,转换失败即登记数据损坏报错。这样 ORM 不 import contracts(依赖方向 contracts ← execution 服务),契约层保持零依赖纯净(test_contract_purity 不受影响)。

### D4: 托管后端双轴配置用受控 JSON 列 + 未核验哨兵,不建第三张表

`hosted_config` JSON 列存 axes/地区/驻留/保留删除/内部工具范围/网关路径/来源/核验时间;缺省时整块为 `{"verification": "unverified"}` 并由 registry 强制:未核验块 MUST NOT 写入任何"满足私有部署"语义值。单条 JSON 优于子表——当前消费方只有绑定门禁(后续 step),查询需求未成立;若 step11 发布清单需要逐字段查询再拆表(退出条件写入 registry docstring)。

### D5: builtin 回填三段式迁移,principal 不回填

两个 alembic revision:(1) expand——建两张表,约束先宽松;(2) migrate+contract——为每个 Agent 以确定性 ID 生成 builtin Deployment(backend_type="builtin"、access="in-process"、axes=内置 harness+企业/无环境、issuer_domain 取部署配置常量)、设默认标记、收紧 NOT NULL。principal 零回填:不可映射负责人按待治理登记(新表 `governance_pending` 轻量行或复用 audit 记录——取后者,避免第三张表)。回填幂等:重复执行不产生第二条 builtin Deployment(以 agent_id+backend_type 唯一索引兜底)。

### D6: workspace 隔离经 Agent 关联链,不在 Deployment 上冗余 workspace_id

Deployment.workspace 语义 = 其 Agent 的 workspace(agent → version → deployment 均同 workspace 不变式)。registry 查询必经 join Agent 校验,负例测试断言跨 workspace 拒绝且不泄露存在性(统一 NotFound 语义)。不冗余列——避免双写漂移;若第二支 RunModel 查询热点需要,再以生成列/视图解决。

## Risks / Trade-offs

- [import 面扫描防不住反射式写表] → 与既有 W1-W3 检测同级别;反射路径本就在"未核验"清单(原基线 §6),登记不假装已封闭。
- [hosted_config JSON 缺 schema 校验会让脏数据进表] → registry 写入前用 `OwnershipAxes.from_dict` + 必填键校验,坏块拒绝写入;测试覆盖脏 JSON 负例。
- [回填的确定性 ID 依赖生成规则稳定] → ID 由稳定前缀+agent_id 派生(非随机),迁移重放幂等;规则写死在迁移文件内。
- [身份链类型先于消费者落地可能返工] → 类型仅 dataclass+校验,第二支消费时若需调整属兼容期内的草案修订(step3 契约同样标记未稳定);在 design 记录此宽容度。

## Migration Plan

expand → migrate → contract 三段(见 D5);回滚:保留新表(方案 step4 迁移/回退要求"回滚应用时保留新表,禁止自动丢弃运行记录"),降级应用后旧代码忽略新表即可。待治理项写 audit,随 audit 保留。

## Open Questions

(无——表归属、回填范围、隔离方式均已定;principal 的 IdP 映射细节字段(saml/oidc subject 格式)在实现时按 `enterprise/auth` 现有 JWT provider 的 claim 形状对齐,不影响契约。)
