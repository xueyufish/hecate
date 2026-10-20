# Spec Delta

## MODIFIED Requirements

### Requirement: Run report view

The Run Report view SHALL let the user select a completed run and display per-metric score distributions, a low-score item list (lowest scores first) where each row reveals the score's `reasoning` on click, and a `source` indicator (`llm_judge` / `deterministic` / `human`) visually distinguishing score origin. The `source` indicator SHALL fall back to rendering an unknown `source` value as its raw string using a neutral outline badge, and SHALL NOT enter a broken render state when that value collides with an object prototype property name (such as `constructor` or `__proto__`).

#### Scenario: Drill into a low score
- **WHEN** the user clicks a score row in the low-score list
- **THEN** the row expands to show the evaluator reasoning text and the source badge

#### Scenario: Unknown source value renders as raw text

- **WHEN** a score row carries a `source` value outside the known set, including names that collide with object prototype properties such as `constructor`
- **THEN** the row renders that raw string as the source indicator with a neutral outline badge, and the view does not crash or render a function/object placeholder

## ADDED Requirements

### Requirement: Run report 查询状态明确呈现

Run Report 视图的 Run 列表、评分分布与低分列表三路查询 SHALL 各自区分加载中、成功空态与查询失败三种状态。查询失败 SHALL 消费并展示 `detail.error.{code,message}` 错误结构并提供手动重试,SHALL NOT 把失败呈现为"No scores recorded for this run."等空数据语义。评分分布与低分列表 SHALL 相互独立:任一路失败 SHALL 不清除、不掩盖另一路已成功的结果。Run 列表查询失败时 SHALL 在 Run 选择区域呈现错误与重试入口;真实空列表 SHALL 呈现无可选 Run 的空态而非错误。重试 SHALL 按当前选中 Run 与既有取数范围重新发起。

#### Scenario: Run 列表失败与重试

- **WHEN** Run 列表查询返回 403 或 500
- **THEN** Run 选择区域显示错误态、该 `code/message` 与重试按钮,不显示为空 Run 列表;点击重试后重新请求列表并恢复既有行为

#### Scenario: 真实空列表不冒充错误

- **WHEN** Run 列表查询成功且过滤 `completed` 后为空
- **THEN** 界面呈现无可选 Run 的空态,分布与低分列表保持未选择提示,不出现错误态

#### Scenario: 一路失败不清除另一路成功结果

- **WHEN** 评分分布查询失败而低分列表查询成功
- **THEN** 分布区域显示错误态与重试入口,低分列表保持已成功内容;反向(分布成功、评分失败)同理,失败侧不渲染"No scores recorded"文案

### Requirement: Run 报表查询的迟到响应丢弃

切换选中的 Run SHALL 立即隐藏旧 Run 的分布与低分内容(呈现加载态)并收起已展开的评分行;旧 Run 查询的成功与错误响应 SHALL 一律丢弃,包括 A→B→A 回切后返回的首个 A 请求。重试 SHALL 始终针对当前选中的 Run 发起,SHALL NOT 以旧 Run 的响应更新当前视图。时间窗口参数变化 SHALL 视为对当前选中 Run 的重新查询,并受同样的丢弃约束;评分排序(按 value 升序)与低分列表取数范围 SHALL 保持既有语义。

#### Scenario: 切换 Run 隐藏旧内容并清空展开

- **WHEN** Run A 的分布与低分列表已展示且某评分行处于展开状态,用户切换到 Run B
- **THEN** 旧内容立即被加载态替换,展开行收起,A 的数据不再展示

#### Scenario: 迟到成功与迟到错误均被丢弃

- **WHEN** 切换到 Run B 后,A 的分布或评分响应(无论成功或失败)晚于 B 的响应返回
- **THEN** 该过期响应被丢弃,B 的视图内容与状态不受影响

#### Scenario: A→B→A 回切不串数据

- **WHEN** 用户从 Run A 切到 Run B 再切回 A,A 的首个请求在回切后才返回
- **THEN** 该过期响应被丢弃,视图展示回切后针对 A 重新发起的查询结果

#### Scenario: 重试始终针对当前 Run

- **WHEN** Run B 的分布查询失败后用户点击重试
- **THEN** 重试请求以 Run B 为 `run_id` 重新发起,不使用先前 Run 的标识

#### Scenario: 未选择 Run 不宣称评分为空

- **WHEN** Run 列表仍在加载、查询失败或成功返回但没有可选的 completed Run
- **THEN** 分布与评分区域仅显示选择 Run 的引导提示，不显示“No scores recorded for this run.”，且不发起评分或分布查询

#### Scenario: 单侧重试不使另一侧的在途查询失效

- **WHEN** 分布或评分查询失败并重试，而另一侧的首次查询仍在进行
- **THEN** 重试只重新请求失败侧，另一侧响应返回时仍能独立呈现；分布与评分的重试互不影响

#### Scenario: 同一 Run 时间窗口重查丢弃旧响应

- **WHEN** 同一 Run 的旧分布与评分请求尚未返回，时间窗口变化触发新查询，随后旧请求成功或失败
- **THEN** 旧响应均不覆盖新查询结果；请求仍只携带既有 Run 参数，不额外扩大时间过滤语义
