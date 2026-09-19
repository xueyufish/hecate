# memory-provider-contract Specification

## Purpose

定义 MemoryProvider 的分层契约:把记忆后端从单一检索接口扩展为覆盖检索、fact CRUD 与对话前注入/回合后写回的完整协议,使第三方记忆后端可以整体接管 Hecate 的记忆工具层,而 builtin(hecate-memory)只是该契约的默认实现。

## Requirements

### Requirement: 分层能力契约

MemoryProvider 契约 SHALL 按三层组织,provider 可声明支持的能力范围:

- **tier-1 通用检索**:`search`——跨集合/命名空间的混合检索(现状能力,保持兼容)。
- **tier-2 记忆检索**:事实记忆检索(L3 用户记忆 + L4 知识记忆,支持 query/top_k/tags 过滤)与会话召回检索(transcript 级,由 `conversation-recall` capability 定义参数)。
- **tier-3 fact CRUD**:事实记忆写入(`add`)、修正(`update`,带 revision 乐观并发语义)与删除(`forget`,软删除)。
- **生命周期钩子**:`prefetch`(LLM 调用前按当前对话检索相关记忆)与 `sync_turn`(回合结束后将本轮交互写回后端)。

Provider SHALL 通过能力声明暴露其实际支持的 tier;核心组合根按声明路由调用。

#### Scenario: 默认后端实现完整契约
- **WHEN** 平台以默认配置启动(`MEMORY_PROVIDER=builtin`)
- **WHEN** 内置 hecate-memory provider 完成注册
- **THEN** tier-1/tier-2/tier-3 与生命周期钩子全部可用

#### Scenario: 第三方后端声明部分能力
- **WHEN** 一个第三方 provider 仅声明 tier-1 与 tier-2(不支持 tier-3 CRUD)
- **WHEN** Agent 调用 `memory_update` 工具
- **THEN** 工具返回结构化错误信息(说明当前记忆后端不支持修正操作),执行路径不崩溃,不产生部分写入

#### Scenario: 后端缺失或工厂失败
- **WHEN** `MEMORY_PROVIDER` 指向未安装的 provider 或工厂初始化抛出异常
- **THEN** 组合根降级为"无记忆后端"(与现状一致),检索类调用返回空结果,写入类调用返回结构化错误,chat 主路径不受影响

### Requirement: prefetch 注入语义

当 `MEMORY_PREFETCH_ENABLED` 开启且活动 provider 支持生命周期钩子时,系统 SHALL 在每次 LLM 调用前,以当前对话内容为查询执行 `prefetch`,并将返回的相关记忆以独立的记忆上下文块注入 prompt。注入 SHALL 满足:

- 记忆块位于 KV-cache 保护前缀区之后(不破坏前缀缓存);
- 记忆块内容计入当轮 token 预算记账(与 4.13 预算轴协同,可被预算处理器裁剪);
- 每轮至多注入一个记忆块,记忆条目数与总 token 受配置上限约束;
- prefetch 失败或超时 SHALL 降级为跳过注入(warn 日志),绝不阻塞或失败当轮对话。

#### Scenario: 正常注入
- **WHEN** 用户开启 `MEMORY_PREFETCH_ENABLED` 且 provider 支持 prefetch
- **WHEN** 一轮对话执行 LLM 调用
- **THEN** prompt 中出现一个记忆上下文块,包含按当前对话检索出的相关记忆条目(条目数 ≤ 配置上限),该块计入预算快照

#### Scenario: 前缀缓存不被破坏
- **WHEN** 连续两轮对话开启 prefetch
- **THEN** KV-cache 保护前缀区(系统提示 + 安全区)内容不因记忆块变化而变化,记忆块始终位于前缀区之后

#### Scenario: prefetch 失败降级
- **WHEN** prefetch 查询超时或抛出异常
- **THEN** 当轮 LLM 调用照常执行,无记忆块注入,日志记录 warn 级事件

### Requirement: sync_turn 写回语义

当活动 provider 支持写回钩子时,系统 SHALL 在回合结束后以非阻塞方式将本轮对话交互提交给 `sync_turn`。写回失败 SHALL 仅记录日志,不影响回合结果;写回范围遵守 workspace 隔离(仅提交当前 workspace 的会话数据)。

#### Scenario: 回合后写回
- **WHEN** 一轮对话正常结束且 provider 支持 sync_turn
- **THEN** 本轮对话内容被提交给 provider 写回,执行不阻塞回合响应返回

#### Scenario: 写回失败不影响业务
- **WHEN** sync_turn 抛出异常
- **THEN** 回合结果不受影响,日志记录 warn 级事件