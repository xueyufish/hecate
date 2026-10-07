# platform-scenario-pack Specification

## Purpose

为企业 Agent 平台演进提供一个可重复、可追溯的架构评测场景包:以统一的 manifest 关联场景、断言与 P01—P08 能力映射,区分 CI 确定性场景与记录基线,使后续演进步骤能对照同一基线比较行为、权限与副作用结果。

## Requirements

### Requirement: Scenario manifest is the single source of truth

场景包 MUST 提供一份机器可读的 manifest(建议 `tests/scenarios/manifest.yaml`),每个场景条目 MUST 声明:场景 ID、对应的 P01—P08 问题组、入口类型、断言摘要、支持或未支持的能力。未支持的能力 MUST 显式标注(如 `unsupported` / `deferred` 及原因),不得以缺省或静默跳过表达。`docs/refactor/platform-evolution-baseline.md` MUST 引用 manifest,不复制其内容。P01—P08 每个问题组 MUST 在 manifest 中出现,或声明覆盖状态为覆盖/部分覆盖/未支持。

#### Scenario: Manifest coverage is complete

- **WHEN** 校验脚本检查 manifest
- **THEN** P01—P08 每个问题组都有条目,且每条目标明覆盖状态;缺少条目或缺少覆盖状态时校验失败

#### Scenario: Unsupported capability is explicit

- **WHEN** 某能力(如图检索,对应 P04)在本轮不验证
- **THEN** manifest 对应条目标注 `deferred` 及原因,且引用该能力的场景不计为覆盖通过

#### Scenario: Baseline doc references manifest

- **WHEN** 基线文档陈述 P01—P08 映射或场景清单
- **THEN** 它引用 manifest 中的场景 ID,manifest 变更时基线文档无需手工同步场景明细

### Requirement: Tier separation between deterministic CI and recorded baselines

场景 MUST 划分为两层:Tier 1(CI 确定性层)只依赖 stub 模型、in-memory SQLite 与 stub 企业工具,随 pytest 在 CI 运行;Tier 2(记录基线层)依赖真实模型或产生非确定性结果,以记录产物(JSON 及说明)形式存在,MUST NOT 在 CI 中执行。权限与副作用断言 MUST 使用确定性断言;内容质量断言 MUST 使用明确 rubric 并记录 evaluator 版本。

#### Scenario: Tier-1 runs hermetically

- **WHEN** 在无网络、无真实模型凭据的环境中运行场景包的 CI 部分
- **THEN** 全部 Tier 1 场景通过,不发起真实外部调用

#### Scenario: Tier-2 stays out of CI

- **WHEN** 查看 CI 测试收集范围
- **THEN** Tier 2 的跑批脚本与记录产物不被 CI 收集或执行

### Requirement: Five repeatable end-to-end scenarios

场景包 MUST 通过真实执行入口提供五个可重复场景,并以业务结果断言(不逐字比较模型文本):正常执行(读取材料并产出预期产物)、拒绝动作(未授权写被拒且有证据)、等待审批(高风险动作进入审批等待,批准后继续)、后端失联(执行后端不可用时状态显式,不虚报成功)、重复提交(同一动作重复提交不产生第二次副作用)。入口按场景语义选择其真实所在层:入口级行为(正常执行、拒绝、审批、后端失联)走真实 HTTP 聊天入口;恢复与幂等语义(重复提交)在引擎 ToolWorker 执行边界上断言——该层是回执与恢复语义的实际所在。每个场景 MUST 在 manifest 中登记入口类型与断言摘要。

#### Scenario: Normal execution produces expected artifact

- **WHEN** Agent 通过入口读取授权材料并执行摘要任务
- **THEN** 任务完成且产物符合预期 schema,断言比较业务结果

#### Scenario: Denied action leaves evidence

- **WHEN** Agent 请求未授权的写操作
- **THEN** 动作被拒绝,拒绝记录可查询

#### Scenario: Approval gates high-risk action

- **WHEN** Agent 触发标记为需审批的动作
- **THEN** 执行等待审批,批准后动作继续,拒绝则不执行

#### Scenario: Backend loss is explicit

- **WHEN** 执行后端在动作后失联或注入故障
- **THEN** 状态显式标记未知/待对账,不报告成功

#### Scenario: Duplicate submit has no second side effect

- **WHEN** 同一动作被重复提交或恢复
- **THEN** 受保护副作用只发生一次,重复请求得到一致响应或冲突拒绝

### Requirement: Permission negative cases across entries

场景包 MUST 提供权限负例:viewer 角色尝试写操作、跨租户访问资源、未经审批的受保护写、MCP 入口与 REST 入口对同一请求返回一致授权结果。每个负例 MUST 断言拒绝结果与可查询证据。

#### Scenario: Viewer cannot write

- **WHEN** viewer 角色通过入口请求写操作
- **THEN** 请求被拒绝且拒绝证据可查

#### Scenario: MCP and REST produce same authorization result

- **WHEN** 同一未授权请求分别经 MCP 入口与 REST 入口提交
- **THEN** 两者的授权结果一致,且均基于服务端可信上下文

#### Scenario: Cross-tenant access is rejected

- **WHEN** 请求访问其他租户的资源
- **THEN** 请求被拒绝

### Requirement: Side-effect recovery negative cases

场景包 MUST 覆盖不确定副作用的恢复语义:已记录调用但结果缺失时,受保护写入 MUST 停止并进入待对账,不得盲目重试;恢复路径 MUST 返回真实结果引用而非占位文本;同一动作键参数变化 MUST 被拒绝。

#### Scenario: Missing result stops protected write

- **WHEN** 注入"动作已执行、结果未落盘"的故障后触发恢复
- **THEN** 系统不重复执行写操作,状态进入待对账

#### Scenario: Recovery returns real result

- **WHEN** 对已成功且结果可读的动作触发恢复
- **THEN** 恢复返回真实结果引用,不是占位文本

### Requirement: Synthetic corpus with version, ACL, and citation positions

场景包 MUST 内嵌少量合成文档,每份文档 MUST 携带版本、权限(ACL)标注和标准引用位置;语料清单 MUST 记录文档与属性。未授权文档 MUST 不可经检索或工具读取;引用 MUST 能定位到声明的引用位置。

#### Scenario: Citation resolves to declared position

- **WHEN** 场景断言引用来源
- **THEN** 引用可定位到语料清单声明的标准位置(如段落/表格单元格)

#### Scenario: Unauthorized document is not retrievable

- **WHEN** 以无权限主体检索受 ACL 保护的文档
- **THEN** 检索结果不包含该文档

### Requirement: Golden samples assert structure, not text

场景包 MUST 保留旧路径的响应协议与事件样本(golden 文件),以 stub 模型录制;断言 MUST 针对协议结构(字段、事件类型序列、状态语义),MUST NOT 对模型文本做逐字相等比较。golden 文件 MUST 标注录制时的代码路径与用途,作为后续入口迁移(如 step5)的比较基线。

#### Scenario: Protocol change breaks golden test

- **WHEN** 响应协议结构字段变化
- **THEN** 对应 golden 断言失败,提示协议漂移

#### Scenario: Model text change does not break golden test

- **WHEN** stub 模型的回答文本变化但协议结构不变
- **THEN** golden 断言仍通过

### Requirement: Independent review chain scenario

场景包 MUST 提供完整验收链场景:读取材料 → 产出摘要 → 独立复核 → 人工批准 → 写入测试工单。复核 MUST 由独立于起草 Agent 的第二主体(独立 Agent 登记与独立会话)执行,其输入只含草稿与复核指令;复核未通过时 MUST NOT 产生工单副作用,也 MUST NOT 产生审批事件;复核通过后的写入 MUST 经审批门禁。断言 MUST 使用确定性断言:材料读取次数、复核者实际收到草稿文本、主体身份区分、审批事件顺序与副作用计数;内容质量复核属 Tier 2/step10 的 rubric 范围,MUST NOT 在本场景中以平均分替代。

#### Scenario: Review rejection blocks the write

- **WHEN** 复核主体否决草稿
- **THEN** 工单服务零副作用,且不产生任何审批事件

#### Scenario: Approved review leads to exactly one write

- **WHEN** 复核通过且人工批准
- **THEN** 工单服务恰被执行一次,APPROVAL_ASKED 与 APPROVAL_DECIDED 成对出现且先于写入,最终答复引用真实工单标识

### Requirement: Cost baseline fields in Tier-2 records

Tier 2 记录产物 MUST 携带成本基线字段块,显式声明采集状态、未采集原因、对应门禁与采集时的字段清单(token 分类、usage 来源 reported/estimated、价格版本、货币与金额、延迟)。确定性 rubric 运行 MUST 将采集状态标为未采集并写明"无模型调用"原因,不得记为零成本。以真实模型运行并采集用量时,MUST 逐字段区分 reported 与 estimated 并固定价格版本,不得将估算标记为最终成本;真实成本基线的可用性 MUST 以 G4(reported/estimated/reconciled 用量记账)关闭为前置。

#### Scenario: Deterministic run records no-cost state

- **WHEN** 运行确定性 rubric 基线并生成记录
- **THEN** 记录的 cost 块状态为未采集,写明原因与门禁指向,不出现零成本数值

#### Scenario: Model-backed run records usage provenance

- **WHEN** 以真实模型运行基线并采集用量
- **THEN** 记录逐字段区分 reported/estimated 并固定价格版本,估算不标记为最终成本

### Requirement: Standalone-consumption scenario group (SC)

场景包 MUST 在 manifest 中维护独立的 SC(standalone-consumption)场景组,条目对齐演进方案 §八验收矩阵的 SC 行(干净安装与无控制面冷启动、结构化库存读取与越权调用、本地批准写入与重启、受管断连与授权过期、重连与重复控制命令、本地审计不可写与中心上传不可用、制品篡改/不兼容与回滚、默认遥测与外部模型数据流、完全隔离网络配置、独立升级 Runtime/宿主)。每个 SC 条目 MUST 声明:场景 ID(`SC<nn>`)、所属运行模式(独立/受管/条件性)、断言摘要、责任 step 及门禁 change;状态为 `planned` 时 MUST 绑定门禁并 MUST NOT 存在实现测试,不得以 skip 或空目录冒充交付。SC 场景随其责任 step 对应 change 交付后才可翻转为 `implemented`,翻转时 MUST 存在与其断言对应的测试;既有 S/P 组条目与 ID MUST NOT 因 SC 组引入而改变。

#### Scenario: SC entry declares gating before delivery

- **WHEN** 校验脚本检查 manifest 中的 SC 条目而其责任 step 对应 change 尚未交付
- **THEN** 条目状态为 `planned`、写明责任 step 与门禁 change,且场景包目录中不存在以该场景 ID 为前缀的实现测试

#### Scenario: SC entry flips only with delivery

- **WHEN** 某责任 change 交付了某 SC 场景的独立运行/受管运行验证
- **THEN** 该条目翻转为 `implemented` 且存在对应测试;仅注册清单或宣称完成而测试缺失时,校验失败

#### Scenario: SC group addition leaves S/P entries intact

- **WHEN** SC 组加入 manifest
- **THEN** 既有 S01—S11 场景条目与 P01—P08 覆盖状态保持不变,SC 组不改变其语义或 ID

### Requirement: SC fixture carries the business rules

SC 场景的模拟库存 API fixture MUST 自身承载业务规则:提供两个相互隔离的数据域、只读身份与需审批的测试写动作;域隔离、角色拒绝与审批门禁 MUST 由 fixture 在业务 App 侧强制执行并以确定性断言验证,未授权读取/写入的拒绝记录 MUST 可查询。库存仅为示例业务域,fixture MUST NOT 将示例业务领域(库存、定价、客户管理等)实现为 Hecate 平台模型,MUST NOT 以 RAG、向量库或 Memory 为前提;结构化业务查询走业务 API,不依赖文档检索。fixture 级测试只验证 stub 自身行为,不声称独立宿主能力。

#### Scenario: Read-only identity cannot cross domains

- **WHEN** 持只读身份的调用方请求另一数据域的库存数据
- **THEN** fixture 拒绝该请求并留下可查询的拒绝记录,两数据域内容互不可见

#### Scenario: Write requires approval inside the fixture

- **WHEN** 需审批的写动作在未获批准时被调用
- **THEN** fixture 不产生状态变更;获批后再次调用时恰好执行一次,重复调用不产生第二次变更

#### Scenario: Fixture stays self-contained

- **WHEN** 检查场景包的 SC fixture 与其测试
- **THEN** 不存在新增的 Hecate 业务领域模型,场景运行不要求安装 RAG、向量库或 Memory 组件

### Requirement: Standalone baseline document binds to the manifest

独立消费基线文档(`docs/refactor/standalone-consumption-baseline.md`)MUST 引用 `tests/scenarios/manifest.yaml` 作为 SC 场景的唯一事实源(引用而非复制),其能力支持状态登记(MUST 区分 supported / unsupported / unverified)MUST 与 manifest 中 SC 条目状态一致:manifest 标记 `planned` 的场景,基线文档对应能力 MUST 登记为未支持/未验证并写明责任 step,MUST NOT 登记为已通过。该文档 MUST 保持快照性质:链接并引用 `platform-evolution-baseline.md` 的既有证据,MUST NOT 覆盖或重写其结论。

#### Scenario: Baseline doc references the manifest

- **WHEN** 独立消费基线文档陈述 SC 场景清单或验收规格
- **THEN** 它引用 manifest 中的 SC 场景 ID,manifest 变更时基线文档无需手工同步场景明细

#### Scenario: Status registration never claims undelivered capability

- **WHEN** 某 SC 场景在 manifest 中仍为 `planned`
- **THEN** 基线文档将对应能力登记为未支持/未验证并写明责任 step,不存在该能力已通过验收的表述

### Requirement: SC03/SC06 以 wheel 进程级验收交付

独立 durable 组合的核心场景 SHALL 以干净安装 wheel + 真实子进程 + 持久文件的进程级验收交付:SC03(本地批准写入与重启)断言受保护写入在持久事实下恰好执行一次,进程硬终止并重启后已决动作回填不重做、等待可合法唤醒、未知结果不重写(以业务 API 调用计数与持久 Task/Action/命令回执为断言对象);SC06(本地审计不可写)断言证据存储不可写时新保护动作停止(拒绝带显式类别且可查询)而 readonly 派发继续。场景实现 SHALL 与 `manifest.yaml` 的状态/切片登记一致,受管组合(SC04/SC05)未覆盖的进程级缺口 SHALL 显式登记而非标完成。

#### Scenario: SC03 硬终止重启后写入不重做

- **WHEN** wheel 安装的 durable runner 完成一次受保护写入后进程被硬终止,再以同一持久库重启
- **THEN** 已决写入回填真实结果(业务 API 计数不变),等待可经合法唤醒继续,未知结果保持待对账,全程无第二次业务写入

#### Scenario: SC06 证据不可写停止保护动作

- **WHEN** 证据目录不可写时派发新的受保护动作
- **THEN** 动作被拒绝(显式类别、可查询的拒绝记录),readonly 派发不受影响;恢复可写后保护动作恢复执行

#### Scenario: 场景清单与实现一致

- **WHEN** 场景清单一致性检查运行
- **THEN** SC03/SC06 的进程级实现、SC04/SC05 的切片登记与缺口说明由清单和测试共同钉住,无不实的完成标记
