# Proposal

## Why

演进方案(`docs/refactor/enterprise-agent-platform-evolution-plan.md`)的 step2 要求正式停止"每个竞品功能都在核心实现"的规划方式,让定位、决策记录、架构文档、功能目录、路线图和结构化清单对齐到已定稿的平台定位。当前状态:定位文档已部分修订但仍链接失效路径(`../research/`→`docs/refactor/` 目录移动的遗留),缺少交付边界/部署模式与业务 App 消费者契约章节;`architecture.md` 无控制面/执行接入层/信任边界描述;核心架构决策(托管 harness 双轴、能力协商、状态所有权)无 ADR 记录;`roadmap.md` 仍以历史 Sprint 排期组织未来工作;`feature-inventory.yaml`(347 条,schema 1)缺少方案 §五要求的责任/实现方式/强制执行点/状态 owner 治理字段。step3 的执行契约设计与这些文档决策直接耦合,必须先定稿。

## What Changes

- **定位文档**(`docs/design/positioning.md`):新增交付边界与三种部署模式(独立/受管/完整私有平台)章节;登记业务 App 私有化交付的消费者契约(中性表述,业务 App 为示例场景;技术预览/独立生产/受管生产分级门槛;HTTP/JSON 首入口);将供应商托管 Agent 服务列为正式执行后端并声明"企业自托管 Hecate ≠ 所有绑定后端满足私有部署/数据驻留";修复指向已移动研究文档的失效链接;对与既定定位矛盾的矩阵表述做一致性修订(竞品矩阵的全面证据复核登记为后续工作,不在本 change 展开)。
- **架构文档**(`docs/design/architecture.md`):增量补充控制面、执行接入层、可替换后端与信任边界;标注七能力域(Agent Engineering/AgentOps/Control Plane/Governance/安全/评测/MCP-A2A 接入层)的现有归属与目标子包;固定允许的依赖方向与跨包公开接口规则;保留模块化单体,不预设微服务拆分。
- **新增两份 ADR**(编号实施时按 INDEX 取下一空闲号,不预占):一份记录核心治理语义(平台保证 vs 实现提供方、内置 Runtime 地位、托管 harness 与 Sandbox 的独立选择、事件/会话所有权、能力协商、不承诺无损状态迁移);一份批准包名/目录迁移方向(`hecate-runtime`/`hecate-runner`、依赖方向、兼容层期限)。
- **功能目录重排**(`docs/features/feature-catalog.md`):按方案 §六映射执行——保留全部现有 ID,新能力追加未使用后缀;将合并/替代/废弃状态从"待完成"分离;为已实现能力标注处置分类(core-contract / builtin-reference / optional-component / integration / retire-candidate);为托管执行准入、供应商会话对账、托管工具强制入口、数据驻留门禁、独立 Runtime 发行包、受管注册/限时授权等新增可验收条目;核对 P01—P08 映射的新增平台责任(Memory/Knowledge 各自绑定、整体服务与组件化检索分别认证、来源同步/索引水位/权限删除、实验快照/逐样本/阶段诊断);登记七类治理能力(身份/策略/调度/审批/发布/证据/资产目录)的治理责任五元组;登记 Runtime、Memory/Knowledge、Evaluation、Observability、Gateway 五能力契约 owner/发布单元/状态 owner/支持窗口;标注七能力域覆盖与缺口。
- **路线图重排**(`docs/features/roadmap.md`):未完成部分改为方案 §七的迭代切片(I-A…I-H)与里程碑(M-S…M-R)结构;跨后端治理里程碑纳入真实托管服务验证(不写为单一供应商专属);已完成 Sprint 历史迁入历史说明章节,不再作为未来排期;同样承载七能力域覆盖标注的受管表。
- **清单治理升级**(`docs/features/feature-inventory.yaml` + `scripts/feature_inventory.py`):schema 1→2,增量字段 `responsibility`/`implementation_mode`/`provider_or_adapter`/`enforcement_point`/`state_owner`/`milestone`/`superseded_by` 及枚举校验;catalog/roadmap 受管区域确定性生成与逐字段漂移检测;CI 严格模式对新/变更条目与 production 声明强制验收字段,历史未补证据条目保留显式欠账;research/Labs 条目补充 owner、验证假设、投入边界、复核触发与退出决定。
- 不修改产品源代码;本 change 交付文档、规划数据与清单工具。

## Capabilities

### New Capabilities

(无——清单治理能力已存在,本次为其扩展需求。)

### Modified Capabilities

- `feature-inventory-governance`:新增 schema v2 治理字段及枚举/显式不适用规则;catalog/roadmap 受管区域生成与逐字段漂移检测;严格模式对新/变更条目和 production 声明的准入口槛(历史条目显式欠账而非静默通过);research/Labs 条目的生命周期治理字段。

## Impact

- **文档**:`docs/design/positioning.md`、`docs/design/architecture.md`、两份新 ADR(编号实施时按 INDEX 取下一空闲号,当前预期为序列尾部 033 之后)、`INDEX.md`;`docs/features/feature-catalog.md`、`roadmap.md`、`feature-inventory.yaml`。
- **工具与测试**:`scripts/feature_inventory.py`(schema v2 字段、受管生成、漂移检测、严格规则)及其单元/回归测试;CI 接入清单校验(含严格模式)。
- **依赖前置**:G5 修复已合入(#194,extract 按 ID 合并语义已有 spec),本 change 在其上叠加,不重做。
- **不受影响**:产品源码、API 契约、数据库 schema;竞品对照全面复核(另行安排);定位文档中历史竞品事实除与新定位直接矛盾的表述外不做逐条重调研。
- **写作约束**:`positioning.md`/`architecture.md` 受 writing-style 数字/日期规则约束(`docs/features/` 与 `adr/` 豁免);消费者契约使用中性"业务 App"表述。
