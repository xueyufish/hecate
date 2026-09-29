# Proposal

## Why

演进方案(`docs/research/enterprise-agent-platform-evolution-plan.md`)在 step1 追加了"独立消费增量":业务 App 只安装所需执行组件、可私有化交付这一独立消费需求登记后,独立运行、受管运行与完整私有平台成为分别验收的交付目标,必须在 step5 交付独立执行组件**之前**固定其验证基线——执行链依赖闭包、三模式部署拓扑、SC 验收场景规格与责任差距表。当前仓库只有 `tests/test_runtime/test_runtime_self_sufficiency.py` 的导入隔离证明,不能替代发行包干净安装与端到端执行验证;`WorkflowExecutionService` 与 `core/composition/agent_execution_port.py` 仍涉平台定义/ORM 与生产装配,`pyproject.toml` 无独立 Runtime 发行包。方案 §七首轮 change 拆分表将本 change(`standalone-consumption-baseline`)列为独立产品最短路径的起点,其证据是后续 `runtime-shared-assembly`、`runtime-standalone-distribution` 等 change 的对比基线。

## What Changes

- 新增独立消费基线文档 `docs/research/standalone-consumption-baseline.md`(快照性质,不覆盖 `platform-evolution-baseline.md` 的既有结论,以链接引用原证据),内容对应方案 step1 增量的四项文档交付:
  - **实施快照**:本次实施所用提交、在途 change、包版本。
  - **依赖闭包**:从实际生产执行链梳理定义加载、图编译、Worker、身份/策略、模型/工具、checkpoint、证据与启动配置的依赖(含 `runtime/AGENTS.md` 登记的函数内 import);逐项写明代码位置、是否执行必需、可由宿主注入/可选安装/待解除耦合及责任 step;核对 wheel 依赖,不以 import 探针通过代替可独立安装。
  - **三模式拓扑**:独立、受管、完整私有平台的信任与数据流拓扑;另记网络模式、可信身份来源、任务/执行状态 owner、凭据与数据出口;受管断连明确授权有效期及重连责任方,不沿用在线撤权保证。
  - **验收规格与差距表**:按 SC 场景登记当前支持/未支持/未验证及责任 step(step5 安装与只读执行、step6/7 持久化与本地强制策略、step10/11 证据与制品门禁、step16 组合认证);每项挂实现 owner、验收 owner 的待指派字段,不虚构人员。延期项(托管组合数据流随 step4、真实成本基线随 G4)如实登记为条件性,不在本 change 强行完成。
- 在 `tests/scenarios/manifest.yaml` 追加 **SC 场景组**(对齐方案 §八验收矩阵的 SC 行:干净安装冷启动、结构化库存读取与越权、本地批准写入与重启、受管断连与授权过期、重连与重复命令、审计不可写、制品篡改/回滚、遥测与数据流、完全隔离网络(条件性)、独立升级),条目初态为 `planned` 并绑定责任 step/建议 change 名;保留既有 S/P 组 ID 不变。
- 新增模拟库存 API fixture(业务 App 侧 stub:两个隔离数据域、只读身份、需审批的测试写动作;库存只是示例业务域,可替换为任意结构化业务 API),业务规则放在 fixture 内;不新增 Hecate 业务领域模型,不引入 RAG/向量库前置。fixture 自身行为(域隔离、只读拒绝、审批门禁)以确定性测试验证——这是 fixture 正确性验证,不声称独立宿主能力。
- 扩展 `tests/scenarios/test_manifest_consistency.py`:SC 前缀的结构与状态语义(planned 必须绑定门禁,不得有实现测试)与独立消费基线文档的引用义务。
- 不修改产品源代码;不声称独立运行目标已实现,不以永久 skip 当完成。

## Capabilities

### New Capabilities

(无——本 change 不引入新系统能力。)

### Modified Capabilities

- `platform-scenario-pack`:场景包扩展 SC(standalone-consumption)场景组——manifest 中 SC 条目的状态与门禁语义、SC fixture 的业务规则边界(示例业务的规则留在 fixture、不进平台)、独立消费基线文档对 manifest 的引用义务(引用而非复制)。

## Impact

- **新增**:`docs/research/standalone-consumption-baseline.md`;`tests/scenarios/` 下的 SC manifest 条目、模拟库存 API fixture 及其 fixture 级测试。
- **修改**:`tests/scenarios/manifest.yaml`(追加 SC 组,不动 S/P 条目)、`tests/scenarios/test_manifest_consistency.py`(扩展一致性规则)。
- **CI**:新增 fixture 级确定性测试随 pytest 运行;无网络、无真实模型、无外部系统依赖。
- **前置/后续**:不改 API 契约、数据库 schema 与生产源码;SC 场景的实现测试由其责任 step 对应 change(`runtime-standalone-distribution`、`standalone-durable-actions`、`managed-runner-enrollment` 等,名称沿用方案建议)交付,届时翻转 manifest 状态。
- **文档关联**:方案 step1 增量清单的 6 个未勾选项由本 change 关闭;`platform-evolution-baseline.md` 保持快照不动。
