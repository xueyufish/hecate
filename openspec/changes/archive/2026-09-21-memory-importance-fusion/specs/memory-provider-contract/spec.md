# Spec Delta

## ADDED Requirements

### Requirement: 检索分数语义

`search_memories` 返回的 `score` SHALL 具有单一明确语义:偏置关闭时为归一化相关性分,开启时为相关性 × 元数据乘数的融合分;每条结果 SHALL 在元数据中携带分信号分解。契约文档 SHALL 说明该语义,使调用方可以安全地做阈值判断。

#### Scenario: score 语义随开关变化

- **WHEN** 偏置关闭与开启各执行一次相同查询
- **THEN** 关闭时 score 即相关性分,开启时 score 为融合分;两种状态下元数据均含分信号分解,调用方可区分

## MODIFIED Requirements

### Requirement: prefetch 注入语义

当 `MEMORY_PREFETCH_ENABLED` 开启且活动 provider 支持生命周期钩子时,系统 SHALL 执行 prefetch 并将返回的相关记忆以独立的记忆上下文块注入 prompt。检索时机 SHALL 以会话为单位钉住:会话开始时以会话开场内容为查询执行一次融合检索并钉住记忆集,整合(consolidation)完成后刷新一次;SHALL NOT 在会话中途逐轮重排记忆集。注入 SHALL 满足:

- 记忆块位于 KV-cache 保护前缀区之后(不破坏前缀缓存);
- 记忆块内容计入当轮 token 预算记账(与 4.13 预算轴协同,可被预算处理器裁剪);
- 每轮至多注入一个记忆块,记忆条目数与总 token 受配置上限约束;
- prefetch 失败或超时 SHALL 降级为跳过注入(warn 日志),绝不阻塞或失败当轮对话。

#### Scenario: 正常注入

- **WHEN** 用户开启 `MEMORY_PREFETCH_ENABLED` 且 provider 支持 prefetch
- **WHEN** 会话首轮执行 LLM 调用
- **THEN** prompt 中出现一个记忆上下文块,包含按会话开场内容融合检索出的相关记忆条目(条目数 ≤ 配置上限),该块计入预算快照

#### Scenario: 前缀缓存不被破坏

- **WHEN** 连续两轮对话开启 prefetch
- **THEN** KV-cache 保护前缀区(系统提示 + 安全区)内容不因记忆块变化而变化,记忆块始终位于前缀区之后

#### Scenario: 会话内记忆集字节级稳定

- **WHEN** 同一会话连续多轮对话开启 prefetch
- **THEN** 各轮注入的记忆集内容与顺序保持一致,不因每轮对话内容变化而重排

#### Scenario: 整合完成后刷新

- **WHEN** 会话存续期间该整合单元的整合运行成功完成
- **THEN** 下一次注入使用刷新后的记忆集,刷新过程不阻塞进行中的对话轮次

#### Scenario: prefetch 失败降级

- **WHEN** prefetch 查询超时或抛出异常
- **THEN** 当轮 LLM 调用照常执行,无记忆块注入,日志记录 warn 级事件
