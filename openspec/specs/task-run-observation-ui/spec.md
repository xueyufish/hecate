# Task Run Observation UI Specification

## Purpose

Task/Run 只读观察前端的行为契约:任务列表、任务详情与动作记录展示消费既有查询 API,以准确的六态呈现和状态分列规则反映后端事实——观察入口不得把"不支持""未记录""已领取但已成功"误呈现为其他含义。本能力是纯前端消费切片,不改变任何后端契约。

## Requirements

### Requirement: 任务列表与详情消费既有查询 API

任务列表页 SHALL 经 `GET /api/tasks` 分页展示目标摘要、生命周期状态与创建时间,并提供进入详情的导航;任务详情页 SHALL 经 `GET /api/tasks/{task_id}` 展示完整目标与生命周期状态、列出该 Task 的全部 Run 并允许切换;侧边栏 SHALL 提供任务列表入口。列表和详情 SHALL 提供手动刷新;列表后续页变空时 SHALL 保留返回上一页的入口。Task 路由切换后 SHALL 丢弃先前 Task 查询的成功及失败响应。生命周期为 `unrecorded` 的任务 SHALL 显式显示"未记录",SHALL NOT 推断或显示为已完成。

#### Scenario: 列表分页进入详情

- **WHEN** 工作区存在多页任务,用户翻页并点击某一任务
- **THEN** 列表按分页参数请求并渲染对应页,详情页展示该任务的完整目标、生命周期与 Run 列表

#### Scenario: 未记录生命周期不推断完成

- **WHEN** 某 Task 的 lifecycle_state 为 unrecorded
- **THEN** 列表与详情均显示"未记录",不显示成功/完成类状态

#### Scenario: 空后续页仍可返回
- **WHEN** 用户进入后续页,后端因数据变化返回空 items
- **THEN** 列表显示当前页为空,保留上一页入口和手动刷新

#### Scenario: 任务切换丢弃旧结果
- **WHEN** 用户切换 Task 路由,先前 Task 的查询成功或失败晚于当前 Task 返回
- **THEN** 页面继续展示当前 Task,旧响应与旧错误均不覆盖当前页面

### Requirement: 动作记录按 Run 查询并守卫竞态

动作记录 SHALL 按 `GET /api/runs/{run_id}/actions` 查询当前选中 Run;快速切换 Run 时 SHALL 仅接受当前选中 Run 的响应,过期响应 SHALL 被丢弃而不渲染。后端 `supported=false` SHALL 呈现为"不支持动作查询"标注,SHALL NOT 呈现为"暂无动作";查询失败 SHALL 呈现为错误态并提供手动刷新;真实空账本呈现为"暂无动作"。组件 SHALL 消费并展示 `detail.error.{code,message}` 错误结构。空动作与不支持查询状态 SHALL 保留手动刷新入口。请求生命周期结束后 SHALL 丢弃其响应,即使用户随后切回相同 Run。

#### Scenario: 快速切换Run丢弃过期响应

- **WHEN** 用户快速从 Run A 切换到 Run B,Run A 的动作响应晚于 Run B 的返回
- **THEN** 界面渲染 Run B 的动作记录,Run A 的迟到响应被丢弃

#### Scenario: 不支持与暂无动作呈现不同

- **WHEN** 后端返回 supported=false,或返回 supported=true 且空列表
- **THEN** 前者显示"不支持动作查询"标注,后者显示"暂无动作";两者不混用

#### Scenario: 错误结构被消费

- **WHEN** 任一查询返回带 `detail.error` 的错误响应
- **THEN** 界面呈现错误态并展示该 code/message,提供手动刷新

#### Scenario: 切回相同 Run 不接受旧请求
- **WHEN** 用户从 Run A 切到 B 再切回 A,首次 A 请求晚于新的 A 请求返回
- **THEN** 页面保留新的 A 查询结果,不被首次 A 查询的过期结果覆盖

#### Scenario: 空动作和 unsupported 可以刷新
- **WHEN** 当前 Run 没有动作或不支持动作查询,用户点击刷新
- **THEN** 页面重新查询当前 Run 并展示新事实,不必切换 Run 或重载浏览器

### Requirement: 动作状态分列呈现且不跨语义推断

动作的恢复状态(`state`)与已记录结果(`outcome`)SHALL 分列独立展示:缺失的 `outcome` 显示"未记录",`state=claimed` 且 `outcome=succeeded` SHALL NOT 呈现为执行中语义;`pending_reconciliation` SHALL 显示待对账标记。列表/详情/动作各区域的加载中、空数据与错误态 SHALL 相互独立、不相互冒充。

#### Scenario: claimed加succeeded不显示执行中

- **WHEN** 某动作返回 state=claimed、outcome=succeeded
- **THEN** 恢复状态列显示 claimed 对应文案,结果列显示 succeeded 对应文案,整体不呈现"仍在执行"语义

#### Scenario: outcome缺失显示未记录

- **WHEN** 某动作尚无已记录结果(outcome 缺失)
- **THEN** 结果列显示"未记录",不推断成功或失败

#### Scenario: 待对账标记可见

- **WHEN** 某动作 pending_reconciliation 为 true
- **THEN** 该动作行显示待对账标记
