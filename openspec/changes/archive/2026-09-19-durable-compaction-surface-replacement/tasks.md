# Tasks

## 1. 事件分类与载荷契约

- [x] 1.1 `eventstore.py` 新增四个 `EventType`（`COMPACTION_STARTED` / `COMPACTION_SUMMARY` / `CONTEXT_SURFACE_REPLACED` / `COMPACTION_COMPLETED`），payload 契约按 design D7（含 `start_anchor`/`end_anchor`/`prior_compaction_id`/`threshold_snapshot`）；验证：单元测试断言枚举成员与 payload 字段构造。
- [x] 1.2 fold 对等性测试：含完整压缩 bracket 的日志 fold 后通道状态与不含压缩事件时完全一致（bookkeeping/fold-skip）；`tests/test_runtime/test_logpolicy_fold.py` 扩展。验证：`python -m pytest tests/test_runtime/test_logpolicy_fold.py -q`。
- [x] 1.3 旧读者回退验证：持久化 EventStore 适配器（Postgres 路径）对四个新事件的序列化/反序列化走 unknown-type→CUSTOM 既有契约，不报错不丢载荷。验证：适配器层往返测试（无 DB 环境则用序列化函数级测试）。

## 2. 影子化账本视图（chain 入口公共路径）

- [x] 2.1 实现账本读取与有效 surface 构建：chain.apply 入口读会话 `COMPACTION_*` 事件 → 按 D4/D6 规则（日志序应用、后记区间覆盖重叠）构建派生视图；视图构建与 token 测量同源（同一函数产出）。验证：单元测试——记录两条重叠区间后有效 surface 正确顶替。
- [x] 2.2 锚点校验：应用条目前校验 `start_anchor`/`end_anchor`（规范化 json 哈希）；失配 → 跳过 + warning + 原文放行。验证：单元测试——篡改通道内容后条目被跳过、原文可见、无错误内容注入。
- [x] 2.3 无差别生效验证：未配置 surface_replacement 的链同样应用账本（backend 只决定触发资格）。验证：单元测试——同会话两条链（一条含 backend、一条不含）投影一致。
- [x] 2.4 自愈路径测试：锚点失配 → 原文回流 → 有效 surface 超线 → 重压记新账 → 新账锚点匹配。验证：集成级单元测试走完整自愈环。

## 3. 容量轴触发与 surface_replacement backend

- [x] 3.1 触发判定：PRE_STEP 在 chain 入口评估 `有效 surface ≥ trigger_ratio × context_window`，不参与 `when_over_budget` 短路；`threshold_snapshot.tokens` 记有效 surface 值；启发式估算器（不用 provider anchor）。验证：单元测试——预算已满足但有效 surface 超线时仍触发。
- [x] 3.2 bracket 写入与执行：START（含锁检查：open bracket 且无 stale 判定则拒绝启动）→ 摘要调用 → SUMMARY → SURFACE_REPLACED（记区间+锚点）→ COMPLETED（记 tokens_before/after）；每会话 `asyncio.Lock` 收紧 check-append 窗口。验证：单元测试——正常 bracket 序列事件断言 + 并发双触发仅一个执行。
- [x] 3.3 stale 判定：START 后出现更新的 `TURN_END` 且无 COMPLETED → 视为 stale，允许新压缩。验证：单元测试构造孤儿 START + TURN_END 序列。
- [x] 3.4 QA 门槛：拒绝不缩小的摘要 + 结构校验；失败 = 无 SURFACE_REPLACED、surface 原样、失败尝试留日志、流程落到 TerminationProcessor。验证：单元测试——summarizer 返回劣化摘要时无替换事件、投影走终止兜底。
- [x] 3.5 rolling 再压缩：摘要输入含上次摘要节点；新区间覆盖旧区间；SUMMARY 记 `prior_compaction_id`。验证：单元测试——两次压缩后账本/视图/审计链断言。
- [x] 3.6 保留边界：retain_ratio 尾部永不遮蔽；区间从首个非 system 单元起。验证：单元测试——边界消息仍在投影中逐字存在。
- [x] 3.7 SUMMARY payload 序列化纪律：剥离超大载荷（有界保留模式，复用 `_bounded_retainer` 思路）。验证：单元测试——含大 content/image 的消息压缩后 SUMMARY 载荷有界。

## 4. 配置面、排他与窗口解析

- [x] 4.1 `context_policy.py`：`_PARAM_SPECS["compression"]` 增加 `trigger_ratio`（默认 0.8）/`retain_ratio`（默认 0.16），load-time 校验（范围/类型/未知字段 fail-fast）。验证：单元测试——越界值与未知参数被拒。
- [x] 4.2 排他 fail-fast：composition 装配时校验「任一节点链含 surface_replacement 且 messages 通道 eviction ≠ NoEviction」→ `ChainPolicyError`。验证：单元测试——两种非法组合被拒、合法组合通过。
- [x] 4.3 窗口解析：`model_default_window()`（复用 `_WINDOW_HINTS`）+ 优先级 `execution_context["context_window"]` → hints → backend 启用且无窗口时 fail-fast。验证：单元测试覆盖三级优先与失败路径。

## 5. summarizer seam 与 composition 接线

- [x] 5.1 runtime：`CompactionSummarizer` ABC（plain noun 规范）+ 从 `execution_context` 取用（对称 offloader 模式）；FailurePolicy 已有语义接入压缩级。验证：单元测试用 Stub summarizer 驱动 3.2-3.5。
- [x] 5.2 composition：summarizer 生产适配器（走 `RuntimePort.llm_invoke`，路由 pin 与 cache namespace 隔离为 adapter 细节，不进 runtime）+ `context_window` 注入（读 `ModelProvider.max_context`）。验证：composition 装配测试——execution_context 携带 window 与 summarizer。
- [x] 5.3 checkpoint metadata 记 `last_compaction_id`（superstep checkpoint 既有路径附带）。验证：压缩后 checkpoint 载荷断言。

## 6. 端到端与回归

- [x] 6.1 resume 场景：压缩完成 → 恢复（checkpoint 水合 + 尾重放）→ 后续投影应用账本（摘要+尾部视图）。验证：Pregel 级集成测试。
- [x] 6.2 崩溃场景：孤儿 START（无 TURN_END）→ 会话锁定不可再压；孤儿 START + TURN_END → stale 放行。验证：集成测试。
- [x] 6.3 回归：`python -m pytest tests/test_runtime/test_context_processors.py tests/test_runtime/test_logpolicy_fold.py -q`；四项验证全绿（ruff check / ruff format --check / mypy / pytest 全量）。
- [x] 6.4 文档：ADR-033 落地状态注记更新（移除「not yet implemented」）；`runtime/AGENTS.md` extension point 表补 `CompactionSummarizer` 行；runtime `context_processors.py` 模块 docstring 的「非破坏性」契约补 surface_replacement 例外表述。

## 7. Follow-up 登记（不在本变更实现）

- [x] 7.1 登记 overflow bypass follow-up（provider `CONTEXT_WINDOW_EXCEEDED` 甄别 + 会话级溢出标记 + 强制触发）。
- [x] 7.2 登记生产 `context_budget` 注入缺口 follow-up（composition 注入 `context_budget`/`context_budget_model_default`）。
- [x] 7.3 登记 in-loop recall extension point（D8：账本 + 日志原文已含找回所需全部信息；`_seq` 演进触发条件之一）。
