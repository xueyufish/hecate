# Design

## Context

现状(详见 proposal Why):L3 检索是"按 importance 排序"的空壳;L3/L4 合并按不可通约分数混排;L3 写入路径落 mock 向量;`access_count` 有采集但无消费;`updated_at` 因 `onupdate` 被 access_count 递增连带刷新(base.py `updated_at` 定义),不能作衰减锚点。

融合代数选择"相关性主导 + 有界乘性偏置"而非线性加权求和,理由见 D2;L4 已具备 dense+sparse RRF 混合检索(`vector_store.py`),其相关性分可直接复用。

## Goals / Non-Goals

**Goals**

- L3 检索具备真实语义相关性排序;L3/L4 合并在统一量纲上进行
- 衰减与重要度作为有界乘性偏置可用(默认关闭),附 `score_breakdown` 观测
- 访问信号可安全用于离线评分(防自我强化)
- prefetch 注入缓存友好(会话级钉住)

**Non-Goals**

- recall(`conversation_search`)融合排序——保持向量主导(归档定位 + 游标契约依赖绝对分序)
- 频率进查询时公式(访问频率是排序输出的内生变量,纳入会形成自我强化回路)
- 容量驱逐机制——本变更只做价值评分字段
- L3 上 Qdrant(方案 C)——具名后续,本变更给出行数天花板声明
- per-tenant 权重调参面、`pinned` 布尔、learned reranker(v2+ 方向)
- consolidation 去重相似度路径的改动(已有真实向量要求,独立于本变更)

## Decisions

### D1. L3 语义检索:候选集内 Python cosine(方案 B)

在 scope(workspace + user)过滤后的候选集内,查询向量现场编码一次,Python 计算 cosine。替代方案:(A) 维持无语义(放弃,等于不给 L3 修 bug);(C) L3 上 Qdrant collection(recall 同款)——长期最干净但要存储迁移与全变异步同步,与本变更的排序层焦点纠缠,列为具名后续。

天花板声明:候选获取为 O(scope 内行数),按 user scope 过滤后通常为几十到几百行,可接受;行数显著增长时切换方案 C。候选获取须设上限(缺省 200,配置项),超限按 `importance` + `last_confirmed_at` 预筛。

### D2. 融合代数:相关性主导 + 有界乘性偏置

`final = relevance_norm × decay_mult × importance_mult`,乘积 clamp 下限 0.3×。拒绝线性加权求和的理由:各信号量纲不可加(BM25 无上界、cosine 分布受模型正则化决定)、min-max 归一化依赖候选窗口使绝对阈值失效、手设权重无监督来源、访问频率与检索时钟是排序输出的内生变量。乘性偏置保证语义序大体保持、只在边际重排。

归一化:min-max within candidates。约束:归一化分不得跨查询做阈值判断(`memory_search` 无低信号阈值,recall 的 `low_signal` 不受本变更影响,无违例点)。

### D3. 衰减锚点:新增 `last_confirmed_at` 列

不可用 `updated_at`(被 access_count 递增连带刷新,衰减锚会变成"上次被检索",自引用回路);不可用 `created_at`(consolidation UPDATE 确认后不刷新,与"被确认即刷新时钟"语义矛盾)。新列 insert 时 = created_at,仅 consolidation 的 UPDATE/SUPERSEDE(对 successor)路径刷新。half-life 缺省:L3 episodic 30d、L3 semantic 180d、L4 evergreen(L4 过时走 `superseded_by` 失效,不衰减)。衰减公式 `exp(-ln2 × age_days / half_life)`,读时计算,不物化。

### D4. 重要度乘数映射:0.5 → 1.0×,clamp [0.5×, 1.5×]

线性映射 `mult = clamp(importance / 0.5, 0.5, 1.5)`,importance=0.5(缺省中性)映射为 1.0×——存量缺省值不改变排序。

### D5. 频率:查询时公式外,consolidation 离线价值分

唯一会话去重计数经关联表 `memory_access_sessions(memory_id, session_id)`(unique 约束)精确去重,避免"同会话反复刷分";备选的 JSONB 截断 session 集合因有损被否。consolidation 触碰行时计算价值分:`value = w1 × confirmationFreshness + w2 × accessHeat`(确认新鲜度 half-life 90d;热度经 log1p 压缩、half-life 30d;输出 `components` 可解释字典),仅落字段供观测,不驱动驱逐。检索命中时的计数写入走异步尽力而为(不阻塞检索响应)。

### D6. 偏置开关:默认 false,中性等价

`MEMORY_FUSION_BIAS_ENABLED` 缺省 false,关闭时乘数恒 1.0、行为与纯相关性排序一致。这是实验开关而非功能承诺:`score_breakdown` 上线后以数据决定默认值。不做 per-tenant 权重面(调参面即支持负担)。

### D7. L3 真实向量:写入路径真实化 + 存量回填 + 查询降级

REST 创建路径注入 embedding 服务(`_embed_one` 基建现成);写入失败不阻塞创建,行标记待向量化;一次性幂等回填脚本处理存量;查询时对无真实向量行排除出 cosine 集合、按元数据兜底。检测方式:回填脚本与写入路径维护显式标记,避免运行时猜测向量真伪。

### D8. prefetch 会话级钉住

会话开始以开场内容融合检索一次,钉住记忆集;consolidation 成功完成后刷新。依据:每 turn 重排会打碎 KV-cache 前缀,且现有契约已要求记忆块位于保护前缀区之后。刷新失败保留旧记忆集(降级不阻塞)。

### D9. breakdown 载体:`MemoryFactHit.metadata`

分信号分解(relevance/decay_mult/importance_mult)放入 `metadata` dict,不改契约 dataclass 字段形状(第三方 provider 兼容);`score` 语义在契约文档中显式化(关闭=相关性分,开启=融合分)。

## Risks / Trade-offs

- [min-max 归一化窗口依赖,同一条记忆跨查询分数不可比] → 禁止对归一化分做跨查询阈值;breakdown 暴露原始分量;design 显式声明
- [Python cosine 随 scope 内行数线性增长] → 候选获取上限 + user scope 过滤;天花板写进 proposal;方案 C 为后续
- [偏置实验可能无收益] → 默认关闭、中性等价、随时可关;breakdown 提供可测量的评估闭环;即便归零也保留 L3 修复与合并修复的全部收益
- [存量回填需要批量 embedding 调用] → 幂等脚本、限速、分批;回填前查询侧已有降级兜底,不阻塞上线
- [关联表随访问增长] → 行数 = 记忆数 × 触达会话数,量级可控;清理随记忆软删除级联
- [prefetch 钉住使新记忆延迟到 consolidation 后才可见] → 会话通常短于 consolidation 周期的影响可忽略;`memory_search` 工具实时路径不受钉住影响(agent 仍可主动检索到最新记忆)

## Migration Plan

1. Alembic migration:`memories` / `knowledge_memories` 增加 `last_confirmed_at`(回填 = created_at);建 `memory_access_sessions` 关联表;新增价值分字段(可空)。
2. 部署顺序:先迁移 → 后应用(新代码对旧库容忍列缺失由迁移先行保证)。bias 默认 false,上线即等价纯相关性排序(L3 行为变化:importance 排序 → 语义排序,这是修复)。
3. 回填脚本分批执行;完成后无真实向量的行仅剩"写入时 embedding 失败"的增量,由降级路径覆盖。
4. 回滚:代码回滚即可;bias 开关回滚零迁移;新列为可空新增列,旧代码可读写旧库。

## Open Questions

- 价值分权重 w1/w2 与访问热度半衰期的缺省值——配置项先行,待 breakdown 数据后定(不影响 spec 与任务拆分)。
- 候选获取上限(缺省 200)与 L4 over-fetch 倍数(现 top_k×3)是否需要联动调整——实现时按基准数据定。
