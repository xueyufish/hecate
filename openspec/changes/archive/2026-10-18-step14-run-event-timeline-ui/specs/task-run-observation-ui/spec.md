# Spec Delta

## ADDED Requirements

### Requirement: Run 事件时间线面板游标分页

任务详情页 SHALL 为当前选中的 Run 提供只读事件时间线面板，经既有运行事件查询接口按游标分页加载：`cursor` SHALL 作为不透明字符串原样传递，下一页 SHALL 使用响应的 `next_cursor`，`has_more` 为假时 SHALL 禁用下一页，上一页 SHALL 按已访问游标回退，页面内事件 SHALL 保持服务端返回顺序。手动刷新 SHALL 重置回第一页；切换 Run SHALL 重置分页并以新 Run 的首页替换当前内容。事件行 SHALL 展示事件标识、`kind`、来源、`source_sequence`、`occurred_at` 与 `payload_schema_ref`（存在时）。

#### Scenario: 游标翻页保持服务端顺序

- **WHEN** 用户在事件面板点击下一页再点击上一页
- **THEN** 请求分别携带响应的 `next_cursor` 与回退光标，各页按服务端返回顺序渲染，不重排不拼接重复行

#### Scenario: 空页保留导航信息

- **WHEN** 第二页事件为空，或空页响应仍有 `has_more=true` 与有效 `next_cursor`
- **THEN** 面板保留已访问游标以便返回上一页，并按 `has_more` 与有效游标决定是否允许继续；不得因 events 为空丢弃分页信息

#### Scenario: 刷新重置回第一页

- **WHEN** 用户翻到第二页后点击手动刷新
- **THEN** 面板回到第一页内容并重新加载，后续翻页从第一页重新开始

### Requirement: 旧请求响应不得覆盖当前 Run

事件面板 SHALL 只接受当前 Run 且当前请求发起之后返回的响应；Run 切换（含 A→B→A 回切）或刷新后，先前请求的成功与错误响应 SHALL 一律丢弃。分页重置 SHALL 与请求失效同批生效，不得出现新 Run 内容与旧 Run 游标或旧 Run 行混排。

#### Scenario: A→B→A 回切不串数据

- **WHEN** 用户从 Run A 切到 Run B 再切回 A，A 的首个请求在回切后才返回
- **THEN** 该过期响应被丢弃，面板展示回切后按 A 首页游标重新发起的请求结果

#### Scenario: 慢旧页错误不惊扰新页

- **WHEN** Run A 的第二页请求尚在途中，用户切到 Run B 且 B 首页加载成功
- **THEN** A 第二页随后的失败响应被丢弃，B 的面板状态不受影响

### Requirement: 缺口显式呈现且查询结果不推断完成

`kind=gap` 的事件 SHALL 以缺失标记呈现并显示 `gap.from_sequence` 至 `gap.to_sequence` 的缺失范围，SHALL NOT 静默跳过或当作普通事件。事件为空或 `has_more` 为假 SHALL 仅表示本次查询结果，面板 SHALL NOT 据此展示或推断 Run 完成、Task 成功或失败。

#### Scenario: 缺口事件显示范围

- **WHEN** 某页包含 `kind=gap` 且 `gap` 为 `{from_sequence: 4, to_sequence: 7}` 的事件
- **THEN** 面板在对应位置显示缺失范围标记，而非普通事件行，也不静默省略

#### Scenario: 空事件不推断完成

- **WHEN** 选中的 Run 没有任何事件，或已浏览到 `has_more` 为假的最后一页
- **THEN** 面板分别显示空态与末页提示，不出现"已完成""成功"等完成性表述

### Requirement: 面板错误隔离与重试

事件面板的加载失败 SHALL 呈现错误态并提供手动重试；错误 SHALL 被面板自身状态吸收，任务详情页的其他面板（含动作记录面板）SHALL 不因事件面板错误改变状态或布局语义。重试 SHALL 按当前 Run 与当前游标重新发起请求。

#### Scenario: 面板错误不影响动作面板

- **WHEN** 事件接口返回错误而动作接口正常
- **THEN** 事件面板显示错误态与重试入口，动作面板保持既有内容与状态

#### Scenario: 重试按当前位置重新发起

- **WHEN** 用户在第二页遇到错误后点击重试
- **THEN** 请求携带第二页的游标重新发起，而不是重置回第一页
