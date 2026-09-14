## Purpose

为模型注册表引入显式的发布生命周期：模型必须经调测并由管理员显式发布后才进入应用引用面（`/v1/models` → Create Agent 模型下拉），未发布模型仅管理面可见、仍可内联测试。同时为模型管理面补齐搜索过滤与 provider 级调用次数统计（6.47 + 6.48）。语义承诺为 reference-only 门禁：发布状态只影响引用面可见性，不回收存量绑定。

## Requirements

### Requirement: 模型发布状态与测试证据

模型注册表中的每个模型 SHALL 具有发布状态：已发布（published）或未发布（unpublished，含初始态），以及最近一次模型测试通过的时间证据。系统的管理界面 SHALL 依据这两项数据派生三态展示：未发布（无通过证据）、调测通过（有通过证据但未发布）、已发布。新注册的模型 SHALL 默认为未发布。

#### Scenario: 新模型默认未发布

- **WHEN** 管理员通过自定义模型注册新增一个模型
- **THEN** 该模型发布状态 SHALL 为未发布，且不出现在应用引用面

#### Scenario: 三态徽章派生

- **WHEN** 模型 M1 无测试通过证据、M2 有测试通过证据但未发布、M3 已发布
- **THEN** 管理界面 SHALL 分别将 M1、M2、M3 展示为 未发布、调测通过、已发布

### Requirement: 存量模型回填为已发布

系统升级迁移 SHALL 将迁移前已存在的全部注册模型回填为已发布，保证升级后应用引用面（`/v1/models`）返回的模型集合与升级前一致。

#### Scenario: 升级零破坏

- **WHEN** 迁移前 `/v1/models` 返回模型集合 S，升级迁移完成后再次请求
- **THEN** 返回的模型集合 SHALL 仍包含 S

### Requirement: 发布为测试通过后的显式动作

系统 SHALL 提供「发布」动作：将未发布模型置为已发布。发布 SHALL 为显式调用，且存在门禁：模型尚无测试通过证据时发布 SHALL 被拒绝并返回原因。发布成功后模型立即进入应用引用面。

#### Scenario: 测试未通过时拒绝发布

- **WHEN** 对一个从未通过模型测试的模型 M 调用发布
- **THEN** 发布 SHALL 被拒绝并返回拒绝原因，M 的发布状态不变

#### Scenario: 测试通过后发布生效

- **WHEN** 模型 M 测试通过后管理员调用发布
- **THEN** M 置为已发布，且随后 `/v1/models` 的返回 SHALL 包含 M

### Requirement: 取消发布自由可逆

系统 SHALL 提供「取消发布」动作：将已发布模型置回未发布，无需前置条件；取消发布后模型从应用引用面消失，但管理面仍可见、仍可内联测试，且可再次发布。

#### Scenario: 取消发布后引用面隐藏且可恢复

- **WHEN** 已发布模型 M 被取消发布，随后再次发布
- **THEN** 取消发布后 `/v1/models` SHALL 不再包含 M，且管理面测试仍可用；再次发布后 `/v1/models` SHALL 恢复包含 M

### Requirement: 引用面门禁为 reference-only

应用引用面（`GET /v1/models` 及以其为数据源的模型选择器）SHALL 仅返回已发布模型；管理面列表与内联模型测试 SHALL 不受发布状态限制。已发布的模型被取消发布时，已通过 `llm_config` 绑定该模型的存量 Agent 与工作流 SHALL 继续正常运行，系统 SHALL NOT 在运行时回收或阻断存量绑定。

#### Scenario: 未发布模型对引用面隐藏但对测试开放

- **WHEN** 未发布模型 M 存在于注册表
- **THEN** `/v1/models` SHALL 不返回 M；管理面模型列表 SHALL 返回 M；对 M 的内联测试 SHALL 正常执行

#### Scenario: 存量绑定不受取消发布影响

- **WHEN** Agent A 的 `llm_config` 绑定已发布模型 M，其后 M 被取消发布，Agent A 发起对话
- **THEN** Agent A 的对话 SHALL 继续使用 M 正常执行，不被阻断

### Requirement: 模型测试记录通过证据

模型测试成功时，系统 SHALL 将对应注册模型的最近测试通过时间更新为本次测试时间；测试失败 SHALL NOT 产生或清除通过证据之外的历史记录（未通过即维持/回到无证据语义）。

#### Scenario: 测试通过刷新证据

- **WHEN** 对模型 M 的内联测试成功返回
- **THEN** M 的最近测试通过时间 SHALL 更新为本次测试完成时间，管理界面 SHALL 将 M 展示为调测通过（若未发布）

### Requirement: 有引用的模型禁止删除

删除注册模型前系统 SHALL 执行引用检查：当该模型仍被任何 Agent `llm_config` 或工作流模型配置引用时，删除 SHALL 被拒绝并返回引用方清单；无引用时删除正常执行。取消发布 SHALL NOT 受引用检查限制。

#### Scenario: 有引用时拒绝删除

- **WHEN** 模型 M 被 Agent A 与工作流 W 引用，管理员请求删除 M
- **THEN** 删除 SHALL 被拒绝，响应 SHALL 列出 A 与 W

#### Scenario: 无引用时允许删除

- **WHEN** 模型 M 无任何引用，管理员请求删除 M
- **THEN** 删除 SHALL 正常执行

### Requirement: 发布动作留痕

发布与取消发布动作 SHALL 记录审计事件，包含动作类型、目标模型、操作者与时间。

#### Scenario: 发布产生审计记录

- **WHEN** 管理员 U 发布模型 M
- **THEN** 审计日志 SHALL 新增一条记录：动作=发布、目标=M、操作者=U

### Requirement: Agent 版本提交对未发布模型的警告

Agent 提交版本（1.3.20）时，若快照 pin 的模型未发布，提交 SHALL 正常完成，但响应 SHALL 包含未发布模型的警告信息；警告 SHALL NOT 阻断提交。

#### Scenario: 提交引用未发布模型收到警告

- **WHEN** Agent A 的 `llm_config` 绑定未发布模型 M，对 A 提交新版本
- **THEN** 提交 SHALL 成功创建版本，且响应 SHALL 包含"M 未发布"的警告

### Requirement: Provider 与模型列表搜索过滤

模型管理面 SHALL 支持对 provider 列表与模型列表的关键字搜索（匹配名称与显示名），模型列表 SHALL 另支持按发布状态过滤。

#### Scenario: 关键字搜索模型

- **WHEN** 管理员在模型列表输入关键字 "qwen"
- **THEN** 列表 SHALL 仅返回 model_id 或显示名包含 "qwen" 的模型

#### Scenario: 按发布状态过滤

- **WHEN** 管理员选择发布状态过滤条件"未发布"
- **THEN** 模型列表 SHALL 仅返回未发布（含调测通过）的模型

### Requirement: Provider 调用次数统计

系统 SHALL 为每个 provider 聚合其注册模型的调用次数并在管理面 provider 卡片展示：默认统计最近 30 天滚动窗口，同时提供累计口径。统计 SHALL 以执行链路记录的模型标识聚合后映射到 provider；无法映射到任何注册模型的调用 SHALL 归入「未匹配」桶展示而非丢弃。

#### Scenario: 调用次数映射到 provider 卡片

- **WHEN** 近 30 天内模型 M1（属于 provider P）被调用 120 次
- **THEN** provider P 的卡片 SHALL 展示 30 天调用次数 120

#### Scenario: 孤儿模型归入未匹配

- **WHEN** 近 30 天内存在对未注册模型标识 X 的调用 7 次
- **THEN** 统计结果 SHALL 展示「未匹配」桶计数 7，SHALL NOT 丢弃该部分
