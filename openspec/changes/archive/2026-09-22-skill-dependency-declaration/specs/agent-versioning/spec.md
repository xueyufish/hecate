# Spec Delta: agent-versioning

## ADDED Requirements

### Requirement: 提交版本执行闭包 walk

「提交版本」动作在 pin 直接列出的 skill 后,系统 SHALL 递归解析每个 skill 的 `requires` 字段,把传递闭包中所有解析成功的 skill(直接 + 隐式)按 provider precedence(`project > user > bundled`)解析到具体 `(name, skill_id, provider, version, content_hash)` 五元组,写入 ref-manifest 的 skill 条目(沿用既有 pin 形状,不引入新条目类型)。

#### Scenario: 闭包含传递依赖写入 ref-manifest
- **WHEN** agent v3.skills = [C],C.requires = [A, B],B.requires = [A],全部解析成功
- **THEN** agent v3 ref-manifest 包含 3 条 skill 条目(C, A, B)

#### Scenario: 闭包 walk 沿用 provider precedence
- **WHEN** 隐式 skill A 在 `project` 与 `bundled` 各有一个同名 skill
- **THEN** A 按 `project` 解析(沿用 `skill-provider-registry` precedence)

### Requirement: 闭包不完整时整个提交事务回滚

绑定期闭包 walk 任一节点解析失败(workspace 不存在 / 跨 source 不兼容 / 软删),整个 agent 版本提交 SHALL 在数据库事务层整体回滚,ref-manifest SHALL NOT 部分写入,错误响应 SHALL 点名全部缺漏节点与各自的传递路径。

#### Scenario: 缺漏依赖触发整体回滚
- **WHEN** 闭包 walk 中任何节点解析失败(workspace 不存在 / 跨 source / 软删)
- **THEN** 整个 ref-manifest 写入被回滚,数据库事务回滚,**不**部分写入

#### Scenario: 错误信息点名全部缺漏节点
- **WHEN** 闭包有 2 个缺漏节点 M1 和 M2(M1 via C,M2 via C → B)
- **THEN** 错误响应包含 M1 和 M2 完整传递路径

### Requirement: 隐式依赖不出现在 agent.skills 可见列表

agent.skills 用户可见列表 SHALL 仅显示直接声明的 skill。闭包 walk 解析出的隐式 skill SHALL 只进入 ref-manifest,不进入 agent.skills 列表。

#### Scenario: 隐式依赖只进 ref-manifest
- **WHEN** agent v3.skills = [C],C.requires = [A]
- **THEN** `GET /api/agents/{id}` 返回的 skills 列表只含 C;ref-manifest 包含 C + A

### Requirement: 闭包内同名 skill 合并

当同一 skill 同时出现在直接列表与闭包中,ref-manifest SHALL 合并为单条(同 skill_id),版本取最新。

#### Scenario: 显式与隐式同名 skill 合并为单条
- **WHEN** agent v3.skills = [A, B],B.requires = [A]
- **THEN** ref-manifest 包含 2 条 skill 条目(A, B),A 不重复

### Requirement: 漂移检测覆盖闭包节点

未 pin 条目按 content_hash 比对报告「快照提交后内容已变化」的规则 SHALL 覆盖隐式依赖节点。pinned 条目(version 非 null)SHALL 不参与漂移报告。

#### Scenario: 隐式依赖被内容前进触发漂移报告
- **WHEN** agent v3 ref-manifest 含 A (version=null, implicit via B.requires),A 活行内容前进
- **THEN** 漂移查询报告 A 路径段变更

#### Scenario: pinned 隐式依赖不参与漂移报告
- **WHEN** agent v3 ref-manifest 含 A (version=2, pinned via 5.9d),A 活行内容前进
- **THEN** 漂移查询不报告 A(v2 快照冻结)