# memory-provider-contract Specification(Delta)

## MODIFIED Requirements

### Requirement: 分层能力契约

MemoryProvider 契约 SHALL 按五层组织,provider 可声明支持的能力范围:

- **tier-1 通用检索**:`search`——跨集合/命名空间的混合检索(现状能力,保持兼容)。
- **tier-2 记忆检索**:事实记忆检索(L3 用户记忆 + L4 知识记忆,支持 query/top_k/tags 过滤,namespace 维度含 `team_id` / `actor_id` / `session_id`)与会话召回检索(transcript 级,由 `conversation-recall` capability 定义参数)。
- **tier-3 fact CRUD**:事实记忆写入(`add`,可指定 scope=`actor_scoped` 或 `workspace_shared`)、修正(`update`,带 revision 乐观并发语义)与删除(`forget`,软删除)。
- **tier-4 任务/工具记忆**:Episode 写入、Reflection 检索、Work Context Graph 查询(`reflection_search` / `work_context_query`)。能力由 `task-memory` capability 定义参数。tool call 作为 TOOL event 写入 episode 是该层的子能力,Tool Memory 不另建独立 tier。
- **tier-5 跨线程记忆**:跨 session / 跨 actor / 跨 team 的 fact 共享与检索。能力由 `cross-thread-memory-store` capability 定义参数。
- **生命周期钩子**:`prefetch`(LLM 调用前按当前对话检索相关记忆)、`sync_turn`(回合结束后将本轮对话交互写回后端)。`end_episode` 与 `escalate_failure` 钩子见 `## ADDED Requirements`。

Provider SHALL 通过能力声明暴露其实际支持的 tier;核心组合根按声明路由调用。tier-4 / tier-5 是**可选**;声明缺失则旧 provider 行为不变(向后兼容)。

#### Scenario: 默认后端实现完整契约

- **WHEN** 平台以默认配置启动(`MEMORY_PROVIDER=builtin`)
- **WHEN** 内置 hecate-memory provider 完成注册
- **THEN** tier-1/tier-2/tier-3 与生命周期钩子全部可用
- **AND** 升级后的内置 provider 同时声明 tier-4 / tier-5 与新增钩子

#### Scenario: 第三方后端声明部分能力

- **WHEN** 一个第三方 provider 仅声明 tier-1 与 tier-2(不支持 tier-3 CRUD)
- **WHEN** Agent 调用 `memory_update` 工具
- **THEN** 工具返回结构化错误信息(说明当前记忆后端不支持修正操作),执行路径不崩溃,不产生部分写入

#### Scenario: 后端缺失或工厂失败

- **WHEN** `MEMORY_PROVIDER` 指向未安装的 provider 或工厂初始化抛出异常
- **THEN** 组合根降级为"无记忆后端"(与现状一致),检索类调用返回空结果,写入类调用返回结构化错误,chat 主路径不受影响

#### Scenario: 旧 provider 未声明 tier-4 / tier-5 仍可工作

- **WHEN** 内置 hecate-memory provider 尚未升级到声明 tier-4 / tier-5
- **THEN** 既有 8 个 memory tool 行为不变,新增的 `reflection_search` / `work_context_query` 返回结构化错误(后端不支持),chat 主路径不受影响

### Requirement: prefetch 注入语义

当 `MEMORY_PREFETCH_ENABLED` 开启且活动 provider 支持生命周期钩子时,系统 SHALL 执行 prefetch 并将返回的相关记忆以独立的记忆上下文块注入 prompt。检索时机 SHALL 以会话为单位钉住:会话开始时以会话开场内容为查询执行一次融合检索并钉住记忆集,整合(consolidation)完成后刷新一次;SHALL NOT 在会话中途逐轮重排记忆集。注入 SHALL 满足:

- 记忆块位于 KV-cache 保护前缀区之后(不破坏前缀缓存);
- 记忆块内容计入当轮 token 预算记账(与 4.13 预算轴协同,可被预算处理器裁剪);
- 每轮至多注入一个记忆块,记忆条目数与总 token 受配置上限约束;
- prefetch 失败或超时 SHALL 降级为跳过注入(warn 日志),绝不阻塞或失败当轮对话。

新增:prefetch 在 4.21 后 SHALL 同时按 `task_type` 在 provider.tier-4 检索匹配的反思(reflection),并入记忆块;`task_type` 缺失时跳过反思检索(不阻塞)。

#### Scenario: 正常注入

- **WHEN** 用户开启 `MEMORY_PREFETCH_ENABLED` 且 provider 支持 prefetch
- **WHEN** 会话首轮执行 LLM 调用
- **THEN** prompt 中出现一个记忆上下文块,包含按会话开场内容融合检索出的相关记忆条目(条目数 ≤ 配置上限),该块计入预算快照
- **AND** 若 provider 支持 tier-4,记忆块同时包含 `task_type` 匹配的反思条目

#### Scenario: 前缀缓存不被破坏

- **WHEN** 连续两轮对话开启 prefetch
- **THEN** KV-cache 保护前缀区(系统提示 + 安全区)内容不因记忆块变化而变化,记忆块始终位于前缀区之后

#### Scenario: 会话内记忆集字节级稳定

- **WHEN** 同一会话连续多轮对话开启 prefetch
- **THEN** 各轮注入的记忆集内容与顺序保持一致,不因每轮对话内容变化而重排
- **AND** 反思条目同样字节级稳定,反思在该会话内不重排

#### Scenario: 整合完成后刷新

- **WHEN** 会话存续期间该整合单元的整合运行成功完成
- **THEN** 下一次注入使用刷新后的记忆集,刷新过程不阻塞进行中的对话轮次

#### Scenario: prefetch 失败降级

- **WHEN** prefetch 查询超时或抛出异常
- **THEN** 当轮 LLM 调用照常执行,无记忆块注入,日志记录 warn 级事件

### Requirement: sync_turn 写回语义

当活动 provider 支持写回钩子时,系统 SHALL 在回合结束后以非阻塞方式将本轮对话交互提交给 `sync_turn`。写回失败 SHALL 仅记录日志,不影响回合结果。

写回范围 SHALL 遵守 namespace 隔离(workspace_id + team_id + actor_id + session_id 四层,详见 `cross-thread-memory-store` capability);仅提交当前 namespace 范围内的会话数据。

新增(4.21 后):`sync_turn` SHALL 同时把本轮涉及的工具调用作为 TOOL event 异步写入当前 active episode(`episode_record` 子动作,受 `REFLECTION_ENABLED` flag 约束);若该轮无可写 episode,该子动作 SHALL 跳过(不创建新 episode)。

#### Scenario: 回合后写回

- **WHEN** 一轮对话正常结束且 provider 支持 sync_turn
- **THEN** 本轮对话内容被提交给 provider 写回,执行不阻塞回合响应返回
- **AND** 本轮工具调用作为 TOOL event 写入 active episode(若存在)

#### Scenario: 写回失败不影响业务

- **WHEN** sync_turn 抛出异常
- **THEN** 回合结果不受影响,日志记录 warn 级事件

#### Scenario: 无 active episode 时跳过 episode_record

- **WHEN** 一轮对话结束,当前会话无 active episode
- **THEN** `sync_turn` 跳过 episode_record 子动作,不创建新 episode

### Requirement: 检索分数语义

`search_memories` 返回的 `score` SHALL 具有单一明确语义:偏置关闭时为归一化相关性分,开启时为相关性 × 元数据乘数的融合分;每条结果 SHALL 在元数据中携带分信号分解。契约文档 SHALL 说明该语义,使调用方可以安全地做阈值判断。

新增:tier-2 / tier-4 / tier-5 检索结果 SHALL 在元数据携带 `source_scope`(`actor_scoped` / `workspace_shared` / `team_scoped` / `reflection` / `work_context_node`)字段;`score` 语义统一,scope 不影响 score 计算,仅 metadata 不同。

#### Scenario: score 语义随开关变化

- **WHEN** 偏置关闭与开启各执行一次相同查询
- **THEN** 关闭时 score 即相关性分,开启时 score 为融合分;两种状态下元数据均含分信号分解,调用方可区分
- **AND** 结果元数据含 `source_scope` 字段,标明来源(actor / team / workspace / reflection / graph node)

## ADDED Requirements

### Requirement: end_episode 钩子语义

当活动 provider 支持 `end_episode` 钩子时,系统 SHALL 在任务完结(`episode_close(episode_id)` 调用)后异步触发反思入口。钩子调用 SHALL 由 `task-memory` capability 定义的 `ReflectionEngine` 消费,产出 typed reflection 并经四道闸过滤。钩子失败 SHALL 降级为 warn 日志,不阻塞任务结果返回。

#### Scenario: 任务完结触发反思

- **WHEN** 一个 episode 被显式 `episode_close(episode_id)` 调用
- **WHEN** provider 支持 `end_episode` 钩子
- **THEN** 系统异步触发反思入口,ReflectionEngine 对该 episode 提取 reflection
- **AND** 反思产物的 status 流转遵循 task-memory capability 的四道闸定义

#### Scenario: provider 不支持 end_episode 仍可工作

- **WHEN** 一个 episode 被显式 `episode_close(episode_id)` 调用
- **WHEN** provider 未声明 `end_episode` 钩子
- **THEN** episode `closed_at` 仍被打戳,反思入口降级为该 plugin 内部后台调度(若有),不报错

### Requirement: escalate_failure 钩子语义

当活动 provider 支持 `escalate_failure` 钩子时,系统 SHALL 在任务失败(evaluator confidence < 阈值)重试时主动召回失败 task_type 对应的 approved reflection,注入重试上下文。钩子 SHALL 仅消费 `status='approved'` 的 reflection;`pending / rejected / deprecated` 不被召回。

#### Scenario: 失败重试召回反思

- **WHEN** 一个 task 失败,evaluator 输出 confidence < 阈值
- **WHEN** provider 支持 `escalate_failure` 钩子
- **THEN** 系统主动检索 `use_cases` 含失败 task_type 的 approved reflections,注入重试上下文
- **AND** reflection 召回失败不阻塞重试发起(降级为跳过反思注入)