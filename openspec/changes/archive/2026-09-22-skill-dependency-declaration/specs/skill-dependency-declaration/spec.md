# Spec Delta: skill-dependency-declaration

## Purpose

让 skill 作者通过 SKILL.md frontmatter 的 `requires` 字段声明对其他 skill 的依赖,在创作期校验依赖图(缺失依赖、环、跨 source、trust 反升级),并在绑定期把训练好的闭包写入 agent 版本 ref-manifest,实现「可组合 skill 包」与「声明松散、绑定期冻结」语义。

## ADDED Requirements

### Requirement: SKILL.md frontmatter 接受 requires 字段

SKILL.md frontmatter SHALL 支持一个 `requires` 字段,值为依赖列表。每个元素 SHALL 是一个 JSON object,包含:

- `name`(必填):kebab-case 字符串(`^[a-z][a-z0-9-]*$`),长度 ≤ 64 字符,符合 `skill-provider-registry` 的命名规则
- `provider`(可选):枚举 `bundled` / `user` / `project`;不指定 provider 时,绑定期由解析器按 provider precedence 选取

`requires` SHALL SHALL NOT 引用 plugin source skill(plugin skill 的 require 通过 `plugin.json` 扩展处理,详见 `agent-plugins-ingestion` 增量)。

`SkillModel` SHALL 镜像 frontmatter 的 `requires` 为可空 JSON 列。创建 / 更新 / 导入时,镜像 SHALL 与 frontmatter 内容一致。

#### Scenario: 导入 SKILL.md with requires
- **WHEN** 导入 SKILL.md,frontmatter 含 `requires: [{name: "pdf-utils"}, {name: "csv-tools", provider: "bundled"}]`
- **THEN** 创建 `SkillModel`,`requires` 列存为该 JSON 数组,API 响应包含解析后的 `requires`

#### Scenario: 通过 API 更新 requires
- **WHEN** `PUT /api/skills/{id}` 传入 `{"requires": [{"name": "pdf-utils"}]}`
- **THEN** `SkillModel.requires` 被更新,响应返回新值

#### Scenario: 空 requires
- **WHEN** 创建或更新 skill 时未提供 `requires`
- **THEN** `SkillModel.requires` 存为 `[]`,语义等价于「无依赖」

#### Scenario: 非法 requires 语法被拒
- **WHEN** `requires` 中某项缺 `name` 字段,或 `name` 不符合 kebab-case
- **THEN** API 返回 422 Validation Error,错误信息说明命名规则

### Requirement: 缺失依赖在创作期被拒

创建 / 更新 / 导入 skill 时,系统 SHALL 对 `requires` 的传递闭包执行存在性校验。任意依赖项在本 workspace 中不存在(无对应 skill,或对应 provider 下无同名 skill)SHALL 被拒绝,返回 422 Validation Error,错误信息点名缺漏依赖及其传递路径。

#### Scenario: 直接缺失依赖被拒
- **WHEN** 创建 skill C,`requires: [{name: "missing-skill"}]`,workspace 中无 `missing-skill`
- **THEN** API 返回 422,错误信息包含「missing-skill」

#### Scenario: 传递缺失依赖被拒
- **WHEN** 创建 skill C,`requires: [{name: "B"}]`,B 存在且 B 的 `requires: [{name: "missing-A"}]`,A 不存在
- **THEN** API 返回 422,错误信息包含「missing-A (via B)」传递路径

#### Scenario: 自引用被拒
- **WHEN** skill S 的 `requires` 包含 S 自身
- **THEN** API 返回 422,错误信息说明自引用

### Requirement: 环检测在创作期被拒

创建 / 更新 / 导入 skill 时,系统 SHALL 对 `requires` 的传递闭包执行 DFS 环检测。任意环 SHALL 被拒绝,返回 422 Validation Error,错误信息点名完整环路径(例如 `A → B → C → A`)。

#### Scenario: 两节点环被拒
- **WHEN** 创建 skill A,`requires: [{name: "B"}]`,而 B 的 `requires` 含 A
- **THEN** API 返回 422,错误信息指明 `A → B → A`

#### Scenario: 三节点环被拒
- **WHEN** 三个 skill A / B / C 互相 require 形成环
- **THEN** API 返回 422,错误信息指明完整环路径

### Requirement: requires 跨 source 默认拒绝

`requires` 默认拒绝跨 source 引用。允许矩阵:

- `user` skill 可 require `bundled` 或同 source 的 `user` skill(同 workspace)
- `project` skill 可 require `bundled` / `user` / `project`(同 workspace)
- `bundled` skill 可 require 同 workspace 的 `bundled` skill
- **任何非 plugin source 不可 require plugin source skill**
- `plugin` source skill 的 require 在 `plugin.json` namespace 扩展中处理(详见 `agent-plugins-ingestion` 增量),SKILL.md frontmatter 内 plugin-sourced skill 不应作为 require 目标

跨 source require SHALL 返回 422,错误信息点明 source 不兼容。

#### Scenario: user skill require project skill 被拒
- **WHEN** 创建 `user` skill U,`requires: [{name: "P", provider: "project"}]`
- **THEN** API 返回 422,错误信息说明 user skill 不允许 require project skill

#### Scenario: 非 plugin require plugin source 被拒
- **WHEN** skill S 的 `requires: [{name: "plugin-skill", provider: "plugin"}]`
- **THEN** API 返回 422,错误信息说明 plugin source 不允许作为 require 目标

#### Scenario: 同 source require 允许
- **WHEN** 创建 `user` skill U,`requires: [{name: "U-helper"}]`,U-helper 同为 user source
- **THEN** 创建成功

### Requirement: 绑定期闭包解析

agent 版本「提交版本」流程 SHALL 在 pin 直接列出的 skill 后,递归解析每个 skill 的 `requires`,把所有解析出的 skill(直接 + 隐式)按 provider precedence(`project > user > bundled`,沿用 `skill-provider-registry`)解析到具体 `(name, skill_id, provider, version, content_hash)` 五元组,并写入 agent 版本 ref-manifest 的 skill 条目。

显式 `agent.skills` 列表 vs 隐式 requires 的关系:

- 显式 skill 解析结果优先(直接声明赢)
- 隐式 skill 沿用同一 provider precedence,但 SHALL NOT 进入 `agent.skills` 用户可见列表(只进 ref-manifest)
- 同一 skill 在显式与隐式都出现时,ref-manifest 合并为单条(版本取最新)

#### Scenario: 闭包解析直接与传递 skill
- **WHEN** agent v3.skills = [C],C.requires = [A, B],B.requires = [A],全部解析成功
- **THEN** agent v3 ref-manifest 包含 3 条 skill 条目(C, A, B),全部 pin 同一五元组格式

#### Scenario: 直接声明赢过闭包解析
- **WHEN** agent v3.skills = [A@project],闭包解析 A@bundled
- **THEN** ref-manifest 的 A 条目以 `project` provider 解析结果为准

#### Scenario: 隐式依赖不出现在 agent.skills 可见列表
- **WHEN** agent v3.skills = [C],C.requires = [A]
- **THEN** ref-manifest 包含 C 和 A,但 `agent.skills` 用户可见列表仅显示 C

#### Scenario: 显式与隐式同名 skill 合并
- **WHEN** agent v3.skills = [A, B],B.requires = [A]
- **THEN** ref-manifest 包含 2 条 skill 条目(A, B),A 不重复

### Requirement: 闭包不完整时绑定期 hard-fail

绑定期闭包 walk 若任何依赖项无法解析(workspace 中不存在 / 跨 source 不兼容 / 当前 provider 下活行被软删),agent 版本提交 SHALL hard-fail,错误信息点名全部缺漏依赖,**绝**不部分写入 ref-manifest,**绝**不静默跳过任何依赖项。整个写入操作 SHALL 在数据库事务内完成,任一节点解析失败即回滚。

#### Scenario: 传递缺漏 hard-fail
- **WHEN** agent v3.skills = [C],C.requires = [A, B],A 存在但 B 不存在
- **THEN** agent 版本提交被拒绝,错误信息包含「B (via C)」缺漏路径,ref-manifest 不写入任何内容

#### Scenario: 部分失败整体回滚
- **WHEN** 闭包 walk 中前 5 个节点成功,最后一个节点解析失败
- **THEN** 数据库事务回滚,ref-manifest 不写入任何条目

### Requirement: trust tier 不沿闭包升级

trust tier SHALL NOT 沿依赖图升级:

- community skill requires official skill → community skill 的 trust tier 保持 community
- 但 official skill 内容通过 `(version, content_hash)` 正常 pin 进 ref-manifest

每个闭包节点维持各自 trust tier,在 agent 版本 ref-manifest 中按节点记录。

#### Scenario: community requires official 保持 community
- **WHEN** skill C(trust_tier=community) requires skill O(trust_tier=official)
- **THEN** C 在 agent 版本闭包中保持 community,O 保持 official,均按各自 tier 记录

### Requirement: plugin 卸载标记 dangling skill

plugin 卸载时,系统 SHALL 把所有 `requires` 引用了该 plugin 中任一 skill 的、未绑定任何 agent 版本的 user / project skill 标记为 dangling。dangling skill 的 instructions / metadata 仍可读,但 loader SHALL 跳过该 skill 并要求用户重选依赖。

已绑定 agent 版本的闭包 SHALL NOT 受 plugin 卸载影响(5.9d 的「源删除后 pinned 内容仍可解析」契约覆盖此场景)。

#### Scenario: 未绑定 user skill 在 plugin 卸载后变 dangling
- **WHEN** user skill U.requires = [plugin-skill-X],plugin 包 P 被卸载,U 未绑定任何 agent 版本
- **THEN** U 标记 dangling,API 响应包含 dangling 字段;U 的 instructions 仍可读但 loader 跳过

#### Scenario: 已绑定 agent 版本不受 plugin 卸载影响
- **WHEN** agent v3 ref-manifest 包含 plugin-skill-X(via U.requires),plugin 包 P 被卸载
- **THEN** agent v3 解析时 plugin-skill-X 内容照常由 pin 快照提供

### Requirement: plugin.json requires 通过 Hecate namespace 扩展

Agent Plugins 1.0 `plugin.json` SHALL 通过 Hecate namespace 扩展(`extensions[io.github.xueyufish]`)支持 `requires` 字段,plugin-level require 在该 plugin 包内所有 skill 间共享,适用与 skill 级 require 相同的图校验规则(缺失依赖、环、跨 source)。

plugin.json 验证器 SHALL:

- 接受 `extensions[io.github.xueyufish].requires` 数组
- 沿用既有 closed-manifest 验证:未知顶层字段 warn+忽略,**不**将 plugin-level require 放到顶层
- 在导入时执行图校验;失败则整个插件安装拒绝

#### Scenario: plugin.json with Hecate namespace requires 导入成功
- **WHEN** plugin.json 含 `extensions.io.github.xueyufish.requires: [{name: "helper-utils"}]`,helper-utils 存在
- **THEN** 插件安装成功,插件内所有 skill 的隐式闭包包含 helper-utils

#### Scenario: plugin.json requires 缺漏依赖被拒
- **WHEN** plugin.json 含 `requires: [{name: "missing-helper"}]`(namespace 内),helper 不存在
- **THEN** 整个 plugin 安装被拒,错误信息点名 missing-helper

#### Scenario: 顶层 requires 字段被忽略
- **WHEN** plugin.json 顶层含 `requires: [...]`(未在 namespace 内)
- **THEN** 验证器视为未知顶层字段(warn + 忽略),plugin-level require 不生效