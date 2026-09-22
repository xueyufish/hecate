# Spec Delta: cli

## ADDED Requirements

### Requirement: skill deps 诊断子命令

CLI SHALL 提供 `hecate skill deps` 子命令,接受 `<name>` 位置参数与 `--graph` / `--closure` / `--reverse` 可选标志:

- 默认(无标志):直接 `requires` 列表,JSON 或 rich 表格输出
- `--graph`:从当前 skill 出发,深度优先展开整条依赖链,以 rich 树形图(arrow-and-node)展示
- `--closure`:从当前 skill 出发,深度优先解析传递闭包,返回全部节点的 `(name, provider, version, content_hash)` 列表
- `--reverse`:反向查询,返回哪些 skill 的 `requires` 包含当前 skill(直接 + 传递)

子命令 SHALL 工作在 workspace 上下文(profile 指向的 workspace),无权限时返回标准 CLI 错误。

#### Scenario: 默认输出直接依赖
- **WHEN** `hecate skill deps pdf-report`
- **THEN** CLI 输出 pdf-report 的直接 requires(例如 `[pdf-utils, csv-tools]`)

#### Scenario: --graph 树形输出
- **WHEN** `hecate skill deps pdf-report --graph`
- **THEN** CLI 以树形展示整条传递链,含传递节点与每个节点的 `(name, provider)` 标识

#### Scenario: --closure 输出闭包清单
- **WHEN** `hecate skill deps pdf-report --closure`
- **THEN** CLI 输出闭包所有节点的 `(name, provider, version, content_hash)` 表格

#### Scenario: --reverse 反向查询
- **WHEN** `hecate skill deps pdf-utils --reverse`
- **THEN** CLI 列出所有直接 / 传递引用 pdf-utils 的 skill 名称

### Requirement: agent version --skill-closure 标志

CLI SHALL 在 `hecate agent version <v>` 子命令下提供 `--skill-closure` 标志,输出指定 agent 版本的训练闭包(直接 + 隐式),格式与 `hecate skill deps --closure` 一致。

#### Scenario: 查看 agent 版本闭包
- **WHEN** `hecate agent version v3 --skill-closure`
- **THEN** CLI 输出 v3 ref-manifest 中全部 skill 条目的 `(name, skill_id, provider, version, content_hash)` 表格,显式与隐式合并去重

#### Scenario: 闭包不可用时返回错误
- **WHEN** v3 不存在或 profile 不可访问
- **THEN** CLI 返回标准 not-found / auth 错误,不输出闭包