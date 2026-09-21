# Proposal

## Why

L3 用户记忆的检索是空壳：查询向量生成后即被丢弃（`retrieve_memories` 按 `importance` 排序），REST 写入路径落库的是 mock 向量；builtin provider 合并 L3/L4 命中时按"重要度 vs 向量相似度"两种不可通约的分数混排。4.14（Memory Importance Scoring）与 4.15（Multi-Signal Fusion Retrieval）要解决的就是这层检索质量问题。

本变更将两个 feature 的排序形态定为"相关性主导排序 + 元数据信号做有界乘性偏置 + 独立的离线频率评分"，并以 `score_breakdown` 可观测性提供持续评估闭环。

## What Changes

- **L3 真实语义检索（修 bug，必做）**：`retrieve_memories` 改为候选集内 Python cosine 相关性排序（方案 B；Qdrant 化为具名后续），替换"生成后丢弃 query embedding、按 importance 排序"的现状。
- **统一量纲合并**：`search_memories` 的 L3/L4 合并改为"归一化相关性 × 有界元数据乘数"，替换混合分数直接 sort。
- **REST 写入路径真实向量化 + 存量回填**：`api/memory.py` 创建路径调用 embedding 服务落真实向量；一次性脚本回填存量 mock 向量；查询时对未回填行降级为"无语义信号"。
- **信号采集补齐**：新增 `last_confirmed_at`（衰减锚点，仅 consolidation UPDATE/取代时刷新——`updated_at` 已被 access_count 递增连带刷新，不可用）；新增按唯一会话去重的访问计数伴生列（防融合排序自我强化）。
- **读取时衰减偏置（默认关闭）**：`MEMORY_FUSION_BIAS_ENABLED` 缺省 false；开启后 `final = relevance × decay_mult × importance_mult`，锚点 `last_confirmed_at`，half-life 按 L3 episodic 30d / L3 semantic 180d / L4 evergreen 分层，importance 0.5→1.0× 线性映射 clamp [0.5×, 1.5×]，乘积总下限 0.3×。
- **consolidation 侧离线频率评分（只评分不驱逐）**：按"确认新鲜度 + 访问热度"加权计算记忆价值分并落字段；容量驱逐机制留 v2。
- **consolidation importance 评分细化**：planning prompt 的 importance 输出改为锚定分档语言。
- **`score_breakdown` 可观测性**：`MemoryFactHit.metadata` 携带 per-signal 分解；provider 契约补 score 语义文档。
- **prefetch 缓存友好**：记忆集在会话开始时融合一次并钉住（consolidation 完成时刷新），避免每 turn 重排打碎 KV-cache 前缀。
- **明确不做**：recall（`conversation_search`）保持向量主导，不纳入融合；频率不进查询时公式；不做 per-tenant 权重调参面；不做容量驱逐；L1 memory block 不涉及。

## Capabilities

### New Capabilities

- `memory-retrieval-fusion`: 事实记忆（L3/L4）的信号采集与融合排序——L3 语义检索、相关性归一、有界乘性偏置（衰减/重要度）、信号采集字段（`last_confirmed_at`、唯一会话访问计数、真实向量要求）、`score_breakdown`、降级语义、prefetch 钉住。

### Modified Capabilities

- `memory-provider-contract`: `search_memories` 返回的 `score` 语义从"混合分"改为"融合分"并在契约中文档化；`MemoryFactHit` 携带 per-signal 分解；prefetch 从"每次 LLM 调用前检索"改为"会话开始融合一次钉住 + consolidation 完成刷新"。
- `memory-consolidation`: 整合 UPDATE/取代路径刷新 `last_confirmed_at`；候选 importance 评分采用锚定分档；新增离线记忆价值评分字段（确认新鲜度 + 访问热度，仅评分不驱逐）。

## Impact

- **代码**：`packages/hecate-memory`（`user_memory.py` 检索重写、新增 ranking/scoring 模块、`provider_impl.py` 合并逻辑、`consolidation.py` 锚点刷新与评分、`api/memory.py` 写入向量化）、`src/hecate/core/composition/memory_provider.py`（`MemoryFactHit` 扩展）、`src/hecate/core/config.py`（新 settings）、alembic migration（`last_confirmed_at` + 唯一会话计数列）、`tools/tool/builtin.py` 与 `tools_backend.py`（`memory_search` 结果 breakdown 透传，签名不变）。
- **基础设施**：无新增外部依赖；embedding 调用进入 REST 写入路径（低频写，可接受）。
- **兼容性**：`MEMORY_FUSION_BIAS_ENABLED=false`（缺省）时排序行为与旧版不同——L3 从"按 importance 排序"变为"按语义相关性排序"（这是修复而非破坏，但属可感知行为变化）；API 响应 shape 不变。
- **运维**：存量 mock 向量需一次性回填脚本；`score_breakdown` 为排序调参与灰度提供数据基础。
- **Roadmap**：feature-catalog / roadmap 中 4.14、4.15 的 "weighted fusion ranking" 表述在 archive 时同步改写。
