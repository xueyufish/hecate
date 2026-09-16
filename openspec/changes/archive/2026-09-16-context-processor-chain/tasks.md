# Tasks — 4.13 Context Engine Processor Chain

## 1. 链契约与执行器

- [x] 1.1 新建 `runtime/context_processors.py`：`ContextProcessor` ABC（async `process(units, ctx) -> ProcessorResult`）、`ProcessorResult`（level、severity、tokens_saved、metadata）、`ContextUnit` 数据结构
- [x] 1.2 `unitize()`：按 `tool_call_id` 折叠原子分组 + 配对不变式校验（悬挂 tool result 附加合成输出或整组丢弃）+ 展平回消息列表
- [x] 1.3 `ContextProcessorChain` 执行器：声明顺序执行、satisfied 谓词提前停止、处理器元数据聚合、skip 透传
- [x] 1.4 `FailurePolicy`：熔断（连续 N 败 open / M 次成功 half-open）、冷却阶梯（60s→300s→900s）、anti-thrash（最小节省比 + 最少新消息数）、force 旁路
- [x] 1.5 预算快照聚合发射：levels 词汇表（`warn|drop|compress|terminate`）+ `stop_reason` 字段，仅在有降级发生时发射，复用现有 BUDGET_SNAPSHOT 事件通道

## 2. TokenEstimator

- [x] 2.1 `TokenEstimator` 接口 + `HeuristicTokenEstimator`（4 chars/token，迁移现有 `_estimate_single_message` 逻辑）
- [x] 2.2 `AnchorTokenEstimator`：以最后一次 LLM 响应 provider usage 为锚，内容 fingerprint 标定锚点覆盖范围，新消息启发式增量，session 级存储支持 resume 恢复
- [x] 2.3 `ContextEngine.estimate_tokens` 内部委托 TokenEstimator（保持三方法契约与外部签名不变）

## 3. 一方处理器

- [x] 3.1 `BudgetWarnProcessor`：warn 阈值检测、HintBlock 追加注入（不动 system prompt）、once-per-crossing 去重（阈值状态存会话级元数据）
- [x] 3.2 `ToolResultTruncationProcessor`：迁移 `_truncate_tool_results`（`tool_result_limit` 参数语义不变）
- [x] 3.3 `RoundWindowProcessor`：滑窗丢弃（保留 system + 最新 user unit）+ importance 排序模式（复用 `PriorityContextEngine._score`，仅在可丢区内生效，不破坏受保护前缀）
- [x] 3.4 `OffloadProcessor`：迁移现有 offload 保全逻辑（阈值判断、stub 生成、失败回退压缩）
- [x] 3.5 `CompressionProcessor`：投影后端（迁移 `compress` 调用路径）+ backend 接口预留（`projection` 默认 / `surface_replacement` 预留，选中后者在本期报 NotImplementedError 并有明确错误信息）
- [x] 3.6 `KVCacheAwareProcessor`：切点决策（受保护前缀内不丢不重排）、`cache_hint` 注解产出、命中率计算（provider usage cached/total 字段可得时）
- [x] 3.7 受控终止：剥离末尾 assistant pending tool_calls、保留 system + 最新 user、worker result 置 `stop_reason="token_capped"`、快照记录 `terminate`；删除 `_emergency_truncate`（`_hard_truncate_message` 保留为单条超限预处理特例）
- [x] 3.8 `HintProcessor`：时间间隔 hint、pending task hint、用量临近阈值 hint（buffer 阈值严格小于 warn 阈值的构造校验），全部注入投影层尾部

## 4. Recall 工具

- [x] 4.1 注册 builtin `recall` 工具：读环境 offload 文件返回完整消息块 JSON；路径不存在返回错误结果；只读语义（不写 channel/checkpoint/文件）
- [x] 4.2 stub 检索提示更新：offload stub 内容引导 agent 优先使用 recall（保留 read_file 兼容提示）

## 5. 策略解析与配置

- [x] 5.1 节点配置 schema 扩展：`context_processors`（有序 type+params 列表）、warn 阈值、offload 阈值（`tool_result_limit`/`max_tokens` 沿用既有字段）
- [x] 5.2 load-time fail-fast 校验：未注册处理器类型、未知字段、互斥参数报错并指明字段名
- [x] 5.3 解析链：node > agent > model-capability > platform 默认；model-capability 从 model metadata 读 prompt-caching 支持与窗口（cache-capable → KV-aware-first 默认策略）
- [x] 5.4 `canonical_hash`：resolved policy 规范化 JSON + sha256，暴露到 agent version 元数据与装配日志

## 6. 接线与装配

- [x] 6.1 `core/composition` 默认链组装（warn 关闭，等价旧五步）与注入；`execution_context["context_chain"]` 透传，保留 `context_engine` 兼容路径
- [x] 6.2 `LLMWorker` 切换委托：删除 `_apply_context_pipeline` 静态管线，改为取链调用并消费 chain_report（快照发射点移入链聚合层）
- [x] 6.3 `WorkflowExecutionService` 两处装配点改传默认链；`PregelRuntime` 构造参数扩展
- [x] 6.4 4.11 `context_shaping` 消费 `cache_hint` 注解：cache-capable provider 渲染 breakpoint 语法，其余丢弃

## 7. 测试

- [x] 7.1 等价性黄金测试：warn 关闭的默认链 vs 旧管线在预算内/超预算/offload/压缩四场景下逐消息一致
- [x] 7.2 原子分组测试：窗口边界不拆 tool pair、悬挂 tool result 归一化、multiset 校验
- [x] 7.3 处理器行为测试：warn once-per-crossing、importance 排序不越前缀、offload 阈值与失败回退、受控终止（tool_calls 剥离 + stop_reason + system/最新 user 存活）、hint 三类注入条件
- [x] 7.4 失败策略测试：冷却升级、熔断 open/half-open、anti-thrash 抑制与 force 旁路
- [x] 7.5 策略解析测试：优先级覆盖、cache-capable 默认策略、load-time 校验拒绝、canonical_hash 稳定性
- [x] 7.6 透传测试：无链 execution_context 时行为与现状完全一致（既有 spec 场景回归）
- [x] 7.7 recall 工具测试：正常返回/未知路径/只读断言

## 8. Compaction ADR 与接缝登记（schema 定案，不含实现）

- [x] 8.1 撰写 ADR：四事件 bracket（`COMPACTION_STARTED`/`COMPACTION_SUMMARY`/`CONTEXT_SURFACE_REPLACED`/`COMPACTION_COMPLETED`，终名 ADR 内定）、`shadowed_seqs`、orphan start = in-progress 锁、checkpoint 源语义、与 dsh 方案的映射对照
- [x] 8.2 消费方接缝登记：8.20 回放、time-travel-resume、ADR-030 下游消费者接缝登记表补行、未知 EventType 回落 CUSTOM 的兼容确认

## 9. 文档与验证

> 9.2 已在归档阶段完成（feature-catalog 状态改述 + OpenClaw 归因勘误 + positioning.md 差异化段落）。

- [x] 9.1 更新 `docs/design/engine-design.md` 扩展点清单与 `src/hecate/runtime/AGENTS.md`（新增 ContextProcessor 扩展点条目）
- [x] 9.2 feature-catalog 4.13 状态改述与 OpenClaw "Context Engine" 归因勘误——延后至本 change 归档阶段（`/opsx-archive` 时随 positioning.md 一起处理），此处仅记录
- [x] 9.3 四件套验证：`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/ -q` 全绿
