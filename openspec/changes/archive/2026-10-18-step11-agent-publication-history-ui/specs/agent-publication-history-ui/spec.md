# Spec Delta

## Purpose

Agent 发布历史只读面板的行为契约：在 Agent 详情页消费既有发布凭据查询 API，以准确的四值结论呈现和发布时点证据展示回答"当时为什么被放行"。空历史、查询失败与过期响应 SHALL NOT 被误呈现为其他含义。本能力是纯前端消费切片，不改变任何后端契约。

## ADDED Requirements

### Requirement: 发布历史面板消费查询 API 并按凭据呈现列表

Agent 详情页 SHALL 提供独立"发布历史"面板，经 `GET /api/agents/{agent_id}/publications`（limit/offset 分页）按时间倒序展示凭据列表，每行展示发布时间、版本、操作人与发布结论，并提供手动刷新。行标识 SHALL 为凭据 `id`：同一版本的重复发布 SHALL 各自成行、相互独立展示，SHALL NOT 按版本合并或相互覆盖。操作人缺失时 SHALL 显示"未记录"类回退文案。Agent 改变时面板 SHALL 从第一页开始并清除原 Agent 的展开状态。面板 SHALL 提供分页，后续页变空时 SHALL 保留返回上一页的入口；刷新入口在加载、空数据与错误各状态下 SHALL 均可用。

#### Scenario: 同版本多次发布分别成行

- **WHEN** Agent 的某版本先后被发布两次，产生两条不同 id 的凭据
- **THEN** 列表呈现两行，各自展示自己的时间、操作人与结论，互不合并、互不覆盖

#### Scenario: 分页与空后续页

- **WHEN** 凭据总数超过一页，用户翻到后续页，后端因数据变化返回空 items
- **THEN** 面板显示当前页为空，保留上一页入口与手动刷新，请求携带对应 offset

#### Scenario: 各状态下可刷新

- **WHEN** 面板处于加载中、空历史或查询失败状态，用户点击刷新
- **THEN** 面板以当前分页参数重新查询并展示新事实，不必切换 Agent 或重载页面

### Requirement: 发布结论按封闭四值准确呈现

发布结论 SHALL 按 outcome 逐值映射呈现：`passed`→"门禁通过"、`warn_passed`→"告警模式放行"、`force_bypassed`→"强制放行（绕过门禁）"、`gate_off`→"门禁未启用"。`warn_passed`、`force_bypassed`、`gate_off` SHALL NOT 呈现为"评测通过"或任何通过语义的统一文案；`gate_off` SHALL 呈现为评测未参与判定。历史记录 SHALL NOT 标示为"当前生效版本"——生效状态只由版本面板的发布指针表达。未知 outcome 值 SHALL 回退显示原始字符串，SHALL NOT 被吞掉或误归类。

#### Scenario: 四值结论互不混用

- **WHEN** 历史中存在 outcome 分别为 passed、warn_passed、force_bypassed、gate_off 的凭据
- **THEN** 四行各自显示对应专属文案，force_bypassed 行明确含绕过语义，gate_off 行不含评测通过语义

#### Scenario: 历史行不标当前生效

- **WHEN** 某凭据的版本恰好等于该 Agent 当前发布指针指向的版本
- **THEN** 该历史行仍按凭据事实呈现，SHALL NOT 附加"当前生效版本"标记

#### Scenario: 未知结论回退原文

- **WHEN** 后端返回四值之外的 outcome 字符串
- **THEN** 面板显示该原始字符串，不显示为"门禁通过"等既有文案

### Requirement: 展开证据仅展示发布时点快照

单条凭据 SHALL 支持展开查看发布时点证据：版本配置哈希（`agent_config_hash`）、candidate 评测 Run 的 id 与配置哈希、baseline 评测 Run 的 id 与配置哈希（仅启用回归比较时存在）、冻结的 `gate_config` 快照与 `gate_report` 报告摘要。证据一律取自凭据自身字段：面板 SHALL NOT 为渲染历史结论而查询最新评测、当前门禁配置或当前版本内容。可空证据字段缺失时 SHALL 如实呈现缺失（未启用回归比较、门禁关闭等场景），SHALL NOT 以其他数据冒充或留空不标注。可空 Run ID 与配置哈希 SHALL 分别展示，缺失 ID SHALL NOT 隐藏已记录的哈希；缺失门禁配置 SHALL 保留字段标签。报告缺失 bypassed_by_force 时 SHALL 显示未记录，SHALL NOT 推断为 false。

#### Scenario: 展开显示冻结证据

- **WHEN** 用户展开一条 require 门禁下通过的凭据
- **THEN** 展开区显示该凭据记录的版本配置哈希、candidate Run id 与其配置哈希、发布时的门禁配置快照与报告摘要

#### Scenario: 后续评测变化不改历史展示

- **WHEN** 展开历史凭据时，该 Agent 已补跑新的评测或修改了门禁配置
- **THEN** 展开区仍显示凭据中冻结的 Run 身份与快照，新评测与当前配置不出现在该凭据的证据中

#### Scenario: 无基线时如实呈现

- **WHEN** 某凭据发布时未启用回归比较，baseline 字段为空
- **THEN** 展开区如实显示基线缺失（未记录），不以其他评测冒充基线

### Requirement: 空历史呈现为无凭据而非从未发布

查询成功且无凭据时，面板 SHALL 显示"暂无发布凭据"类文案，并说明发布凭据能力引入前的历史发布没有记录；SHALL NOT 呈现或暗示"从未发布"。

#### Scenario: 空历史不推断从未发布

- **WHEN** 查询某 Agent 的发布历史返回空 items 且 total 为 0
- **THEN** 面板显示"暂无发布凭据"及早期发布可能没有记录的说明，不显示"从未发布"断言

### Requirement: 查询三态独立且守卫过期响应

面板的加载中、空数据与查询失败 SHALL 相互独立、不相互冒充。查询失败 SHALL 呈现错误态并消费 `detail.error.{code,message}` 结构、提供重试；Agent 切换、翻页与手动刷新时，先前请求的成功响应与错误响应 SHALL 均被丢弃，SHALL NOT 覆盖当前内容；同一参数（Agent 与 offset）重复发起请求时，仅最后一次请求的结果可被接受。加载失败与成功后的内容切换 SHALL 不丢失当前分页位置之外的导航能力。

#### Scenario: 切换 Agent 丢弃旧响应

- **WHEN** 用户从 Agent A 页面进入 Agent B 的详情页，A 的发布历史响应晚于 B 的返回
- **THEN** B 的面板展示 B 的凭据（或 B 的空态/错误态），A 的迟到响应被丢弃

#### Scenario: 翻页竞态仅接受当前页

- **WHEN** 用户快速从第 1 页翻到第 2 页，第 1 页的响应晚于第 2 页返回
- **THEN** 面板渲染第 2 页内容，第 1 页的迟到响应被丢弃；再翻回第 1 页重新发起的请求结果正常接受

#### Scenario: 错误结构被消费且可重试

- **WHEN** 查询返回带 `detail.error` 的错误响应
- **THEN** 面板呈现错误态并展示该 code 与 message，提供重试入口；重试成功后正常展示凭据

#### Scenario: Agent 切换重置分页
- **WHEN** 用户在 Agent A 的后续页切换到 Agent B
- **THEN** B 从 offset=0 查询，A 的展开状态和总数不沿用

#### Scenario: 后续页失败保留返回入口
- **WHEN** 已知总数后进入后续页查询失败
- **THEN** 错误和重试保持可见，用户也可点击上一页重新查询
