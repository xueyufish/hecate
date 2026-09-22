# Proposal: Skill Dependency Declaration

## Why

Hecate 当前 skill 体系(5.9 / 5.9-enh / 5.9d)支持单 skill 的版本快照、provider 优先级与 content_hash 指纹,但不支持 skill 间的依赖表达。具体痛点:

- skill 作者无法声明「我需要依赖」,每个 agent 必须自带 `requires` 引用,传递发现完全是手工作业。
- Skill 演化无关联图,影响分析不可行。
- Agent 版本 ref-manifest 只 pin 直接列出的 skill,运行时集合由「pin 直接 + 实时 resolve 间接」组成,后者未经过 frozen 校验,可能出现「直接列表里有 A、A 的间接依赖 B 缺失」的悬挂。

本 change 兑现 catalog 行 5.9e 已写明的承诺(`docs/features/feature-catalog.md` 第 733 行):**在创作期校验依赖闭包,在绑定期把闭包冻结到 agent 版本 ref-manifest**,实现「可组合 skill 包」。这是 5.9d 的 bind-time freeze 语义向传递闭包的扩展。

## What Changes

- 新增 `skill-dependency-declaration` 能力,覆盖 SKILL.md frontmatter `requires` 字段语义、依赖图校验(缺失依赖 + 环检测)、绑定期闭包解析。
- `skill-api` 扩展:POST / PUT / import 端点接受 `requires` 字段,校验失败返回 422 并点名缺漏依赖或环路径。
- `agent-versioning` 扩展:agent 版本「提交版本」流程新增闭包 walk,递归解析 `requires`,把所有解析出的 skill(直接 + 隐式)按 `(name, skill_id, provider, version, content_hash)` 写入 ref-manifest。pinned 条目沿用 5.9d 加载契约,不参与同名 precedence 仲裁。
- `skill-provider-registry` 扩展:`requires` 跨 source(plugin / bundled / project / user 互不 require)默认拒绝,沿用既有 anti-escalation 与 trust tier 语义。
- `agent-plugins-ingestion` 扩展:Agent Plugins 1.0 `plugin.json` 通过 Hecate namespace 扩展(`extensions[io.github.xueyufish]`)支持 `requires` 字段,导入时与 skill 级 `requires` 同样校验。
- `cli` 扩展:新增 `hecate skill deps [--graph|--closure|--reverse]` 与 `hecate agent version --skill-closure` 诊断子命令。

设计取舍(写进决策记录,不展开):

- **D1** 真源 = SKILL.md frontmatter,`SkillModel.requires` 为反规范化 JSON 镜像(只读一致);不引入 join 表。
- **D2** `requires` 语法只允许名字 + 可选 provider 提示,不引入 semver 范围(v1)。
- **D3** 隐式依赖只进 ref-manifest,**不**进 agent.skills 用户可见列表。
- **D5** plugin 卸载时:未绑定、且 `requires` 引用了该 plugin 的用户 skill 进入 dangling 列表;已绑定 agent 版本不受影响(5.9d 源删除语义覆盖)。
- **D8** 闭包不完整时 hard-fail,绝不静默跳过。
- **D9** trust tier 不沿依赖图升级(community requires official → community 仍为 community,但 official 内容被 pin)。
- **D11** Bundle skill(空壳 skill 只含 requires)**推到 5.5d**,不在本 change 范围。

不做什么(写明边界,防 scope creep):

- 无运行时 range 求解器(catalog 明文;声明松散、绑定期冻结)。
- 不改运行时 loader 行为 — 5.9d 的 pinned 加载契约已覆盖隐式依赖。
- 无 BREAKING:存量 skill 无 `requires` 字段,行为不变;新字段全部 additive。

## Capabilities

### New Capabilities

- `skill-dependency-declaration`: SKILL.md frontmatter `requires` 字段声明、图校验(缺失依赖 + 环)、绑定期闭包解析、与 plugin.json `requires` 的统一校验入口。

### Modified Capabilities

- `skill-api`: POST / PUT / import 接受 `requires` 字段并按校验规则拒绝。
- `agent-versioning`: 提交版本流程新增闭包 walk,把递归解析出的全部 skill pin 进 ref-manifest。
- `skill-provider-registry`: `requires` 跨 source 默认拒绝;anti-escalation 语义延伸。
- `agent-plugins-ingestion`: Hecate namespace 扩展支持 `plugin.json` `requires` 字段。
- `cli`: 新增依赖图诊断子命令。

## Impact

**Affected code areas**:

- `src/hecate/runtime/skill/` — 新增闭包解析器 / DFS 图校验模块。
- `src/hecate/runtime/agent/versioning.py` — agent 版本提交流程新增闭包 walk。
- `src/hecate/api/skill.py` — POST / PUT / import 端点接受 `requires`,新增 422 错误。
- `src/hecate/plugin/agent_plugins.py` — `extensions[io.github.xueyufish].requires` 字段解析。
- `src/hecate/cli/skill.py`、`src/hecate/cli/agent.py` — 诊断子命令。

**Schema / migration**:

- `skill` 表新增 `requires JSON NULL` 列;alembic 迁移脚本;存量行 `requires = '[]'`。
- `PluginModel.manifest_json` 已包含 namespace 扩展字段,持久化无需新增列。

**Dependencies / APIs**:

- SKILL.md frontmatter 解析器(已有,扩展 `requires` 字段)。
- Agent Plugins 1.0 manifest 验证器(扩展允许的 Hecate namespace 字段)。

**Risk**:

- 闭包 walk 需 DFS 深度上限(防误用导致栈溢出)。建议深度上限 32,workspace 内 skill 数 < 1000 性能可接受。
- 大量存量 skill 升级后 `requires=[]`,迁移路径安全(只新增可空 JSON 列)。
- 无 BREAKING:旧 skill 无 `requires`,等价于「无依赖」,与今天行为一致。