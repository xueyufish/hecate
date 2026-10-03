# Hecate 独立消费实施基线(standalone-consumption-baseline)

> 性质:独立消费增量的代码事实快照,固定独立运行/受管运行/完整私有平台三类交付目标在本轮实施起点的能力与差距;不承担实时状态源,不跟随后续提交更新。既有结论见 [platform-evolution-baseline.md](platform-evolution-baseline.md)(下称"原基线"),本文件链接并引用原证据,不覆盖、不重写其结论。
> 事实标记:【已证实】= 本文件核验方法可直接复核;【未核验】= 证据不足,禁止当作事实引用。
> 输入关联:治理方案见 [enterprise-agent-platform-evolution-plan.md](enterprise-agent-platform-evolution-plan.md)(下称"方案")§一/§五/§八;场景清单唯一事实源是 `tests/scenarios/manifest.yaml`(含 SC 组),本文件 §5 引用其条目,不复制场景明细。

## 1. 基线声明

| 项 | 值 | 备注 |
|---|---|---|
| 基线分支 | `feat/standalone-consumption-baseline` | 由用户决策以主目录分支承载,未使用 worktree(沿用原基线 §1 的已记录偏离先例) |
| 基线提交 | `a4851bb`("docs(research): align plan with standalone execution (#190)") | 独立消费增量启动时的 `main`;本文全部 `file:line` 以该提交的工作副本为准 |
| 快照日期 | 2026-09-29 | 仅事实记录 |
| 在途 change | `standalone-consumption-baseline`(本 change)、`openspec-spec-hygiene`(任务全勾、未归档) | `openspec list --json` 核验 |
| 关键包版本 | hecate 0.1.0、fastmcp 4.0.0b4、litellm 1.86.1、SQLAlchemy 2.0.50、FastAPI 0.136.3、pydantic 2.13.4;workspace 包 hecate-ops/hecate-llm/hecate-sandbox/hecate-memory/hecate-enterprise/hecate-channel-slack/hecate-channel-feishu 均 0.1.0 | `uv pip list` 核验【已证实】 |
| 核验方法 | 读码 + `grep` + `openspec list` + `uv pip list` + pyproject 静态读取;未做 wheel 安装、独立冷启动或运行时验证 | 运行时断言以测试文件存在性为证据,不宣称已执行 |

## 2. 执行链依赖闭包

目的:回答"业务 App 只安装执行组件时,哪些东西必须随包走、哪些可由宿主注入、哪些当前耦合在主应用待解除"。按方案 step5 的目标结构,`hecate-runtime` 承载内核及执行语义,`hecate-runner` 承载宿主;下表"分类"三值:**宿主可注入**(实现可由宿主装配/替换)、**可选安装**(未安装时启动声明 `unsupported`)、**待解除耦合**(当前代码耦合平台设施,需在对应 step 解开)。本表是静态核对,不是独立运行测试通过的结论;wheel 安装验证由 step5b 交付。

### 2.1 执行链分段闭包

| 链段 | 代码位置 | 是否执行必需 | 分类 | 责任 step | 证据类型 |
|---|---|---|---|---|---|
| ① 启动配置 | `core/config.py` 模块级 `settings` 单例;`EVENT_STORE_BACKEND`(config.py:90,默认 `"memory"`)、`WORKSPACE_ROOT`(config.py:482)、`CHAT_TOOL_LOOP_ENGINE_ENABLED`(config.py:613,默认 false) | 是(执行链各处读 settings) | 待解除耦合:宿主需自带配置面,内核代码不得依赖平台 `settings` 单例 | 5b | 【已证实】读码 |
| ② 定义加载 | `studio/workflows/execution_service.py:24` 顶层 import `WorkflowModel/WorkflowVersionModel`;:391、:1252 函数内 import `AgentModel` 并 `select`(:419—421) | 是(执行需要 Agent/Workflow 定义) | 待解除耦合:平台 ORM 定义查询留在平台 adapter,转换为执行输入(方案 step5a) | 5a/5b | 【已证实】读码 |
| ③ 图编译与内核 | `runtime/compiler.py:26` `GraphCompiler`、:51 `compile()`;`runtime/pregel.py:90` `PregelRuntime`;`runtime/workers/` 十个 worker(tool_worker.py 等) | 是 | 执行必需,随 `hecate-runtime` 包发布;导入隔离已有 subprocess 探针(`tests/test_runtime/test_runtime_self_sufficiency.py`),但无独立安装证明 | 5b | 【已证实】读码 + 探针存在;【未核验】wheel 安装 |
| ④ Worker 策略与上下文 | `runtime/task_allocator.py:21` `TaskAllocator`(ABC)、`runtime/eventbus.py:69` `EventBus`(ABC):执行内选人与消息协作 | 否(平台级调度另设,方案 step6) | 宿主可注入(可选组件) | 5c | 【已证实】读码 |
| ⑤ 身份与策略 | `core/auth_context.py:18` `AuthContext`(数据类);`core/deps_workspace.py:112` `get_auth_context`、:200—287 角色守卫(FastAPI 依赖);`tools/policy/policy_pipeline.py:27—83` `PolicyDecision/PolicyContext/PolicyLayer`;`tools/gateway/authz.py:36、103` `CallerIdentity/GatewayAuthorizer` | 受保护动作必需 | `AuthContext`/`PolicyLayer` 为中立类型,随包发布;`deps_workspace` 留在平台,宿主提供等价身份/策略 adapter | 5b(类型)/5c+7(adapter) | 【已证实】读码 |
| ⑥ 模型与工具 | `core/composition/runtime_port_adapter.py:63` `_ProductionRuntimePort`、:385 `create_runtime_port(db, llm_service, tool_registry)`;`tools/tool/registry.py:34` `ToolRegistry`、:62 `execute()`;litellm(主应用基础依赖) | 是 | 待解除耦合:adapter 绑定平台 `db` session 与 `llm_service` 单例,须抽共享装配;`ToolRegistry` 由宿主装配;litellm 经 extras 声明 | 5b/5c | 【已证实】读码 |
| ⑦ checkpoint 与会话状态 | `runtime/checkpoint.py:20` `CheckpointStore`(ABC)、:81 `InMemoryCheckpointStore`;`studio/session_state/`(经 `wiring.py:87` 装配) | 可靠执行必需;内存实现仅供开发 | 宿主可注入(ABC 已就位,宿主选择后端) | 5b/5c | 【已证实】读码 |
| ⑧ 证据与事件 | `runtime/eventstore.py:134` `EventStore`(ABC)、:250 `InMemoryEventStore`;`studio/event_state/postgres_store.py:64` `PostgresEventStore`;平台装配根 `core/composition/wiring.py:84—86` `create_event_store(settings)` | 是 | store 宿主可注入;`PostgresEventStore` 可选安装(独立 profile 按需);装配根不随宿主走(宿主自带装配,不读平台 settings) | 5b/5c(装配)、6(持久化 profile) | 【已证实】读码 |

入口层现状(chat.py:373 引擎开关消费、chat.py:451—456 懒加载 `create_runtime_port`、八类执行入口与 N1—N5 缺口)见原基线 §2,本表不重复登记。

### 2.2 runtime 懒加载清单归类(引自 runtime/AGENTS.md,逐行)

| 懒加载行 | 分类 | 说明 |
|---|---|---|
| `runtime/tool_access.py` → tools.tool.shell_analysis | 待解除耦合 | step5b 以注入接口或共享常量解除 |
| `runtime/workers/coordinator_worker.py` → studio.workflows.templates | 待解除耦合 | 模板构造参数化(退出条件已在 AGENTS.md 登记) |
| `runtime/agent_tool.py` → channel.a2a.client | 可选安装 | A2A 交接能力;未安装时 `unsupported` |
| `runtime/compaction.py`、`runtime/context_processors.py` → hecate_memory.consolidation | 可选安装 | RAG/Memory 非独立 profile 前提 |
| `runtime/offloader.py` → hecate_sandbox.environment | 可选安装 | 沙箱卸载能力 |
| `runtime/task_memory_hook.py` → hecate_memory.memory.task_memory + `EpisodeModel` | 可选安装 + 待解除耦合 | ORM import 须按退出条件改注入 |
| `runtime/workers/tool_worker.py` → tools.tool.builtin(名称集) | 待解除耦合 | 小改动,随 step5b |
| `runtime/security/egress.py` → hecate.ops.dlp.* | 宿主可注入 | 已走 DI + TYPE_CHECKING |
| `runtime/security/hooks/output_security.py` → ops.dlp/ops.output_security/findings_writer | 宿主可注入 | 同上,退出条件已登记 |
| `runtime/security/guardrail_assembly.py` → ops.security.findings_writer | 宿主可注入 | 同上 |

## 3. wheel 依赖核对

`[project].dependencies`(pyproject.toml:24 起)末尾硬依赖三个 workspace 包:**hecate-ops(:49)、hecate-llm(:50)、hecate-sandbox(:51)**;`packages = ["src/hecate"]`(pyproject.toml:171)表明 Runtime 内核在主应用包内,无独立发行包。与 §2 闭包交叉后的结论【已证实,静态】:

- **独立 Runtime 发行包不存在**:安装 `hecate` 即安装全部主应用依赖(fastapi、sqlalchemy、fastmcp、litellm、redis 等),不满足"SDK 是便利封装,不安装完整 Hecate"的边界。→ step5b 交付 `hecate-runtime` wheel 与 extras 声明。
- **执行必需但应降为可选的主应用硬依赖**:`hecate-sandbox`(仅 offloader 懒加载)、`hecate-ops`(仅 security 钩子懒加载 DLP/findings_writer)、`hecate-llm`(hub/微调,执行链不用)。step5b 把三者移出独立 profile 的必需闭包;主应用侧收敛按 step19。
- **已是可选安装的先例**:hecate-memory、hecate-enterprise、channels 不在 `[project].dependencies`,经 extras/CI 显式安装——独立包沿用该机制。
- **import 探针通过 ≠ 可独立安装**:`test_runtime_self_sufficiency.py` 在主环境 subprocess 内屏蔽 forbidden prefixes,不能证明干净环境下 wheel 依赖闭包成立;安装性验证由 step5b 的包级构建/安装测试交付,本文件不预先记为通过。

## 4. 三模式部署拓扑

记法沿用原基线 §4(实体 → 边);B1—B5 应用层直连路径及当前阻断见原基线 §4,本节不复制。部署层出口控制标注【未核验】的结论继续有效(TODO-D1)。

### 4.1 独立运行(step5 技术预览起步)

业务 App →(业务身份)→ 执行宿主 `hecate-runner` → 共享执行装配(`hecate-runtime`)→ 已选 adapter(模型/业务工具)→ 本地证据存储。必须部署:宿主、该 profile 必需 adapter、必需存储(方案 §一运行模式表)。

- 网络模式:允许指定外部服务/仅内网/完全隔离;不连控制面 ≠ 不能联网【已证实为方案规则,现状无实现】。
- 可信身份来源:客户配置的本地信任根 + 业务身份 adapter;无平台 tenant 时使用不可混淆的部署/数据域标识(方案 step7)。
- 任务/执行状态 owner:宿主拥有 Task/Run/Action 的权威本地记录,不依赖平台表。
- 凭据与数据出口:adapter 配置只存 secret 引用;本地证据与集中上传分离,默认不外发 prompt/业务记录/产物/trace(方案 §一)。
- 现状:宿主、发行包、本地身份 adapter 均不存在(§2/§3 闭包即证据)。

### 4.2 受管运行(step4/6/7 交付接入)

在 4.1 之上增加:控制面 →(注册/期望配置/命令)→ 宿主;宿主 →(事件投影/命令回执)→ 控制面。

- 定义/授权来源:控制面发布期望配置与限域限时授权;执行方保存实际绑定、执行事实与回执(方案 §一状态与信任规则)。
- 断连语义:授权按期限 + 最大陈旧窗口收窄,过期拒绝;高风险动作要求在线判定或预先批准且当前有效的本地审批;断连不自动降级为独立授权模式。
- 重连责任方:宿主先验证当前授权、撤销与期望配置,再接收新受保护动作;历史事件按游标去重补传;不迁移活跃 Run、不追授历史审批。
- 现状:平台 Task/Run/Deployment 模型、注册/投影/命令通道均不存在(方案 step4/6 范围)【已证实,原基线 §2/§3 无对应表与 API】。

### 4.3 完整私有平台(step16 按 profile 认证)

客户环境内的管理平台 + 执行组件 + 选用基础设施;本地企业管理域集中治理,执行方仍独立管理执行事实。管理数据库与执行数据库分别归属,禁止跨 owner 直接读写或共享事务(方案 §三)。

## 5. SC 验收规格登记

场景明细的唯一事实源是 `tests/scenarios/manifest.yaml` 的 `sc_scenarios` 组(SC01—SC10,title 逐字对齐方案 §八);本表登记当前能力状态,两处由 `test_manifest_consistency.py` 钉住一致性。当前状态枚举:**未支持**(能力未实现,责任 step 未交付)/ **未验证**(代码路径可能存在但无独立安装/运行证据)/ **未支持/未验证**。本 change 阶段所有 SC 场景均为 `planned`,对应状态不得登记为已支持/已通过。

> 更新(2026-10-02,change `hecate-runner-preview`):SC01/SC02 随 step5c 只读技术预览交付翻转为"技术预览已交付"(`implemented`,证据 `tests/scenarios/test_sc01_cold_start.py`/`test_sc02_inventory_read.py`:干净 venv 双 wheel 安装、无控制面冷启动、只读库存读取与越权拒绝、本地证据)。技术预览不构成生产支持;完整认证仍归 step16。原快照结论保留如下,不覆盖。

| SC ID | 能力 | 当前状态 | 责任 step |
|---|---|---|---|
| SC01 | 干净安装与无控制面冷启动 | 技术预览已交付(5c);生产认证 step16 | 5b/5c(step16 认证) |
| SC02 | 结构化库存读取与越权调用 | 技术预览已交付(5c);生产权限 step7 | 5c(生产权限 step7) |
| SC03 | 本地批准写入与重启 | 未支持 | 6/7 |
| SC04 | 受管断连与授权过期 | 未支持 | 7 |
| SC05 | 重连与重复控制命令 | 未支持 | 4/6/7 |
| SC06 | 本地审计不可写、中心上传不可用 | 未支持 | 6/7/10 |
| SC07 | 制品篡改、不兼容与回滚 | 未支持 | 5 最小校验;11/16 完整门禁 |
| SC08 | 默认遥测与外部模型数据流 | 未支持 | 5/7/10、16 |
| SC09 | 完全隔离网络配置(条件性) | 未支持 | 11/16,启用时单独认证 |
| SC10 | 独立升级 Runtime/宿主 | 未支持 | 5/11/16 |

## 6. 差距表

每项挂实现 owner 与验收 owner 的待指派字段(指派时点由用户在对应 change 启动时决定,沿用 2026-09-28 暂缓决定);差距证据指针指向 §2/§3 与原基线。

| 责任 step | 差距项(当前 → 目标) | 实现 owner | 验收 owner | 指派时点 |
|---|---|---|---|---|
| step5a | `WorkflowExecutionService` 平台职责 → 共享执行装配函数与执行应用服务(§2 ②⑥)（已交付:`runtime-shared-assembly` — `runtime/execution_assembly.py` + `execution/builtin.py`） | 已指派（本 change 实现） | 已指派（同左） | change 启动时 |
| step5b | 主应用内 runtime → `hecate-runtime` wheel + extras;懒加载清单按 §2.2 归类解除;干净安装测试 | 待指派 | 待指派 | change 启动时 |
| step5c | 独立宿主(`hecate-runner`):本地 manifest 加载、身份/策略 adapter、最小执行/状态/证据接口;只读 SC01/SC02 技术预览 | 待指派 | 待指派 | change 启动时 |
| step6 | 本地持久任务/Action 意图/领取/结果引用/事件游标;重启恢复与对账(SC03/SC06) | 待指派 | 待指派 | change 启动时 |
| step7 | 本地身份/策略/审批/凭据/预算;受管授权租约与断连边界(SC04/SC05);G4 用量记账 | 待指派 | 待指派 | change 启动时 |
| step10 | 最小本地证据 envelope、导出与脱敏边界(SC06/SC08 扩展) | 待指派 | 待指派 | change 启动时 |
| step11 | 制品门禁:manifest 校验、本地准入/升级/回退(SC07/SC10) | 待指派 | 待指派 | change 启动时 |
| step16 | SC 故障集组合认证、支持矩阵、网络模式与数据流核验(SC01—SC10 收口) | 待指派 | 待指派 | change 启动时 |

## 7. 条件性延期项

以下为方案 step1 明确的条件性工作,本 change 不强行完成,登记延期理由与重启条件:

- **托管执行组合数据流详查**(harness 会话、Sandbox、远程 MCP、供应商内部工具的数据流与驻留/保留限制):登记格式与已核验示例见原基线 §5;实际组合数据流随 step4 `AgentDeploymentModel` 落地时补齐,缺少登记对象(表/字段)时详查无落点。
- **真实成本基线**:受 G4 门槛(reported/estimated/reconciled 用量记账)约束,目标 step7/step10;采集口径已固定于原基线 §9 的 Tier-2 `cost_baseline` 块,当前采集状态为 `not-collected`,是事实登记而非缺陷。
- **多 Agent 成本/结果比较**:随团队协作模型(step12/13)启用;单 Agent 基线口径已固定,比较在模型落地前无从执行。


## 8. step5b 交付增量(runtime-standalone-distribution)

基线提交之后的交付记录(2026-10-03,change `runtime-standalone-distribution`);本节为快照内增量,不回改 §2/§3 的历史结论:

- **发行包已交付**:`packages/hecate-runtime`(import root `hecate_runtime`,88 模块),无 `hecate` 反向依赖(纯净测试 `tests/test_runtime/test_kernel_purity.py` + 探针双模式钉住);`src/hecate/runtime/` 为纯转发 shim,退出条件 step19。§3 的"发行包不存在"结论就此关闭。
- **依赖闭包已解除**:§2.①的 settings/数据库耦合经 `hecate_runtime.config.RuntimeConfig` + `hecate_runtime.memory` 注入接缝清零(grep 归零);§2.2 五行"待解除耦合"全部清除(shell_analysis/dynamic_orchestration/tool_names 收入内核,task_memory 经 gateway,guardrail 拆为纯装配+平台桥接);§3 的三个硬依赖未进内核依赖(pyproject 仅 httpx/pydantic/sqlalchemy/cryptography),主应用侧收敛仍归 step19。
- **安装性验证已建立**:非 editable wheel 在干净 venv 安装 + 无源码路径冒烟(`packages/hecate-runtime/smoke/smoke_run.py`,断言事件序列、checkpoint 持久、无 `hecate.*` 模块载入)本地通过;CI `runtime-wheel-clean-install` job 固化(构建→干净安装→无全量 hecate 断言→冒烟→extras 缺失时 capability 全部 unsupported)。§3"import 探针通过 ≠ 可独立安装"的缺口就此关闭。
- **仍未支持**:SC01/SC02(无控制面冷启动、只读执行)需 step5c 宿主;manifest 不翻转。本节证据不授予独立运行的生产认证。
