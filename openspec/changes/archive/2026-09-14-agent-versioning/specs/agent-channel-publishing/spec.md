## Purpose

提供 Channel 实体作为外部入口与 Agent 已发布版本之间的别名间接层：渠道按两种绑定模式（追踪最新发布 / 钉死指定版本）解析版本，API 渠道获得可寻址的稳定调用契约，IM 实例经渠道路由到 Agent 版本，从而保证外部调用者的配置契约稳定。

## ADDED Requirements

### Requirement: Channel 实体与创建约束

系统 SHALL 提供渠道实体：`type(api|im|embed|webhook)`、所属 `agent_id`、`bind_mode`、按 type 的 `config`。v1 中 `api` 与 `im` SHALL 可路由，`embed` 与 `webhook` SHALL 作为保留枚举存在（创建时接受但标记为未接线，不提供调用面）。创建渠道时，目标 Agent SHALL 已存在至少一个已发布版本。

#### Scenario: 为未发布 Agent 创建渠道被拒绝

- **WHEN** Agent A 从未发布过任何版本，尝试为其创建 api 类型渠道
- **THEN** 创建 SHALL 被拒绝并提示需先发布版本

#### Scenario: embed 类型占位创建

- **WHEN** 为已发布的 Agent A 创建 webhook 类型渠道
- **THEN** 渠道 SHALL 创建成功但状态标记为未接线，不产生任何可调用入口

### Requirement: 两种绑定模式

渠道绑定 SHALL 支持两种模式：`published`（追踪该 Agent 的最新已发布版本——新版本发布后渠道自动跟随）与 `pinned`（钉死到指定版本号，直到显式改绑）。改绑（repoint）SHALL 只修改渠道绑定，SHALL NOT 创建或修改任何版本。

#### Scenario: 发布新版后 published 模式渠道自动跟随

- **WHEN** 渠道 C 以 published 模式绑定 Agent A（当前发布 v3），随后 A 发布 v4
- **THEN** C 的下一次调用 SHALL 解析到 v4

#### Scenario: pinned 模式渠道不受新发布影响

- **WHEN** 渠道 D 以 pinned 模式绑定 Agent A 的 v2，随后 A 发布 v4
- **THEN** D 的下一次调用 SHALL 仍解析到 v2

### Requirement: 渠道寻址的 API 调用

系统 SHALL 提供按渠道寻址的调用入口（OpenAI 兼容的 chat/completions 语义，支持流式），调用时按该渠道的 `bind_mode` 解析 Agent 版本并执行。既有按 `agent_id` 寻址的调用入口 SHALL 保持现状（解析活行，作为未版本化直连面），不受本能力影响。

#### Scenario: 渠道调用按绑定模式执行

- **WHEN** 通过渠道 C（pinned 到 v2）发起 chat/completions 调用，Agent A 草稿与 v2 配置不同
- **THEN** 执行 SHALL 使用 v2 的冻结配置（含 pin 的工作流版本）

#### Scenario: 直连入口行为不变

- **WHEN** 通过既有 `/agents/{agent_id}/chat/completions` 调用 Agent A
- **THEN** 执行 SHALL 使用 A 的活行配置，与任何渠道绑定无关

### Requirement: 显式版本调试头

渠道调用入口 SHALL 接受版本指定参数（请求头或查询参数），显式将本次调用解析到指定已提交版本；指定不存在的版本 SHALL 返回明确错误。

#### Scenario: 指定版本调试

- **WHEN** 通过渠道 C 调用并显式指定版本 1（C 的绑定是 v2）
- **THEN** 本次调用 SHALL 按 v1 快照执行

#### Scenario: 指定不存在的版本

- **WHEN** 通过渠道 C 调用并显式指定版本 99（不存在）
- **THEN** 调用 SHALL 返回明确的版本不存在错误
