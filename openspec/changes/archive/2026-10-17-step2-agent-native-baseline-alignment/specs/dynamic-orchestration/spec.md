# Spec Delta — dynamic-orchestration

## Purpose

定义已交付的 DYNAMIC/COORDINATOR TaskDAG→Pregel 子图结构化执行策略及其观测、预算和隔离契约。该策略不构成开放任务自主执行的必经路径；新增可靠计划修订与已接受决策恢复属于共享持久执行机制的后续交付，不由 channel-only 历史追授。

## ADDED Requirements

### Requirement: TaskDAG 编排限定为结构化执行策略

DYNAMIC 模式与 COORDINATOR 编排 SHALL 作为任务可声明的结构化执行策略提供:适用于目标分解、成员 roster 与依赖明确、需要图调度、Channel 与分支汇合的场景。开放任务的自主执行 SHALL NOT 以生成完整 TaskDAG 或经图编译为必经步骤;动态 Plan 是可修订的执行意图,Todo、直接行动、局部依赖计划与已发布 Workflow 可按任务组合使用。后续声明支持可靠计划修订的 profile SHALL 处理已完成、运行中、未派发与结果未知的工作,不得通过更换任务 ID 绕过待对账动作;该新增能力在 step5e/6/7 交付及验收前登记为待实现,既有 DYNAMIC 不自动取得此保证。Agent 调用 Workflow 与 Workflow 创建 Agent Child Run 按各自父子和产物验收契约关联,普通 LLM 节点不自动升级为自主 Agent。

#### Scenario: 开放任务不强制 DAG

- **WHEN** 一个开放任务经自主执行推进且未声明 DYNAMIC 模式
- **THEN** 执行路径不要求先生成完整 TaskDAG 或编译子图

#### Scenario: 新目标不追授旧实现

- **WHEN** 既有 DYNAMIC 实现仅具备原有 TaskDAG/Channel 与事件能力
- **THEN** 保留原有结构化策略及其已验证范围,可靠计划修订与已接受决策恢复仍登记为待实现,不因本 delta 宣称通过

#### Scenario: 结构化场景继续可用

- **WHEN** 任务显式声明 DYNAMIC 模式并提供 roster
- **THEN** 既有 COORDINATOR/TaskDAG 行为按本规范其余要求继续执行,不受本限定变更影响

#### Scenario: 修订不绕过待对账工作

- **WHEN** 一个声明支持可靠计划修订的 profile 接受涉及结果未知的已派发任务的修订
- **THEN** 修订不得通过为其分配新任务 ID 将该工作标记为未执行;未知结果先按对账规则处理

### Requirement: 计划历史持久化边界

COORDINATOR 子图的 channel 计划历史(`_plan` channel 与 `ORCHESTRATOR_DECISION` 事件)提供图执行可观察性,SHALL NOT 被视为或登记为已接受决策的持久化语义:可靠恢复所需的已接受模型决策、计划修订接受与继续位置持久化由共享持久执行机制承担,其契约随演进方案 step6 的共同执行语义交付。在相应持久化语义交付前,要求可靠执行/恢复的任务 SHALL NOT 仅凭 channel 计划历史宣称具备可靠计划恢复能力。既有事件的 history-preserving 语义(单次编排内 `plan_revision` 单调递增、不重写历史)保持不变。

#### Scenario: 不以 channel 历史宣称可靠恢复

- **WHEN** 评审或支持矩阵描述 DYNAMIC 编排的计划恢复能力
- **THEN** 依据是持久化执行事实;channel-only 计划历史不作为可靠恢复证据

#### Scenario: 既有事件语义不变

- **WHEN** 一次编排迭代产生新的 `ORCHESTRATOR_DECISION` 事件
- **THEN** 既有 history-preserving 契约(修订版本递增、不重写既有事件)保持不变
