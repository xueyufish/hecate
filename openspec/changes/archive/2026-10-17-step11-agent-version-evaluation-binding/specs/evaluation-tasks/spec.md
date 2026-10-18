# Spec Delta — evaluation-tasks

## ADDED Requirements

### Requirement: Agent 评测运行绑定已提交 AgentVersion

离线 answer_source=agent 的任务 SHALL 支持声明 agent_version。Run SHALL 在实际开始时解析已提交版本并保存自身的 agent_id、agent_version、agent_config_hash 和执行快照；后续任务或草稿修改 SHALL NOT 改变该证据归属。绑定运行 SHALL 按快照的 persona、模型、工具/技能名单、技能 pin、知识库引用及模式执行，workflow 模式 SHALL 使用 pin 的 workflow 版本。绑定评测 SHALL 实际生成答案而非复用预存答案；缺少数据项、版本/资源不可解析、调用失败或跨 workspace 引用 SHALL 显式失败，不静默回退草稿。未声明版本 SHALL 保留既有 live 行为；历史 NULL 运行 SHALL NOT 回填。POST /runs 的现有答题来源范围不变。

#### Scenario: 绑定版本按冻结快照实际执行

- **WHEN** 任务绑定 v2，草稿和已 pin 技能内容随后变化，数据项已有预存答案
- **THEN** Run 保存 v2 的身份、哈希和快照，并实际调用 v2 配置和 pin 技能产生答案，不把预存答案当作 v2 的执行结果

#### Scenario: 模式和 workflow pin 生效

- **WHEN** v2 为 workflow 模式且 pin workflow v1，草稿随后改为 chat
- **THEN** 绑定评测仍通过已有入口执行 workflow v1，不改成 chat 或 workflow latest

#### Scenario: 任务修改不污染历史身份

- **WHEN** 已完成的 v2 Run 对应任务改为另一 Agent/版本或被删除
- **THEN** Run 的 Agent、版本、哈希和快照不变，不能被另一个 Agent 的发布门禁借用

#### Scenario: 任务部分更新保留或显式清除声明

- **WHEN** 任务修改阈值、仅修改 agent_version 或显式传 agent_version=null
- **THEN** 未传版本时保留声明，仅传版本时更新声明，显式 null 清除未来运行的声明，旧 Run 均不改写

#### Scenario: 历史和未声明版本保持原状

- **WHEN** 历史未绑定 Run 被读取，或任务未声明 agent_version
- **THEN** 历史运行不被回填，未声明任务按既有 live/预存答案语义执行，均不冒充版本绑定证据

#### Scenario: 不可解析或跨 workspace 引用失败

- **WHEN** 版本或 Agent 不存在/已删除，或者任务、Run、数据集或被引用资源来自不允许的 workspace
- **THEN** 绑定运行失败，不回退 live，不成为已完成版本评测证据

#### Scenario: 终止运行不可重新绑定

- **WHEN** 已终止 Run 被请求重跑，或已有绑定被请求换成另一版本/未声明版本
- **THEN** 请求被拒绝，原有绑定证据保持不变
