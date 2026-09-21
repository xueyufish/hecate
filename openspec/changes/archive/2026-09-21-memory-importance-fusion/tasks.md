# Tasks

## 1. Schema 与迁移

- [ ] 1.1 Alembic migration:`memories` 与 `knowledge_memories` 增加可空 `last_confirmed_at`(回填 = `created_at`)与真实向量标记列,新建 `memory_access_sessions(memory_id, session_id)` 唯一关联表与价值分可空字段;验证 `alembic upgrade head` 在空库与带数据库均可执行, downgrade 可回滚
- [ ] 1.2 `src/hecate/core/config.py` 新增 settings:`MEMORY_FUSION_BIAS_ENABLED`(缺省 false)、half-life 分层配置(L3 episodic 30d / L3 semantic 180d / L4 永不衰减)、重要度乘数 clamp、乘积下限 0.3、L3 候选获取上限(缺省 200);验证单测覆盖缺省值与非法值拒绝

## 2. 信号采集

- [ ] 2.1 检索命中路径接入唯一会话计数:命中时异步尽力而为写入 `memory_access_sessions`(冲突即跳过),`access_count` 语义不变;验证单测:同会话重复命中仅计一次唯一计数,`last_confirmed_at` 不因检索改变
- [ ] 2.2 REST/工具/整合三条 L3 写入路径接入真实 embedding(复用 `_embed_one`);embedding 失败不阻塞创建并标记待向量化;验证单测:mock 服务注入下三条路径均落真实向量,失败路径落标记且创建成功

## 3. L3 语义检索与排序模块

- [ ] 3.1 新增 `hecate-memory` ranking 模块:min-max 归一化、衰减乘数(读时计算,锚点 `last_confirmed_at`,分层 half-life)、重要度乘数(0.5→1.0×,clamp [0.5,1.5])、乘积 clamp 下限 0.3、`score_breakdown` 组装;验证单测覆盖各乘数边界(0.5 中性、无锚点=不衰减、乘积不破下限)
- [ ] 3.2 重写 `UserMemoryService.retrieve_memories`:scope 过滤 + 候选上限预筛(importance+`last_confirmed_at`) + 查询向量一次编码 + cosine 排序;无真实向量行排除出 cosine 集合并按元数据兜底;验证单测:相关记忆胜过高重要度无关记忆、mock 向量行降级不产生相似度噪声
- [ ] 3.3 查询向量化失败降级:warn 日志 + 回退按 importance/时间排序,检索调用不失败;验证单测模拟 embedding 异常

## 4. Provider 合并与契约

- [ ] 4.1 重写 `BuiltinMemoryProvider.search_memories` 合并逻辑:L3 cosine 相关性与 L4 hybrid 相关性归一到统一尺度后合并,bias 开启时套乘性偏置;`MemoryFactHit.metadata` 携带 breakdown;验证单测:L3 相关命中不被 L4 低相关命中压过、开关两种状态下 breakdown 均存在
- [ ] 4.2 `memory_search` 工具结果透传 breakdown(`tools_backend.py`,签名不变);验证:工具集成测试可见分解字段
- [ ] 4.3 契约文档更新:score 语义(关闭=相关性分,开启=融合分)写入 `docs/integrations/memory/third-party-memory.md` 与 `memory_provider.py` 模块 docstring;验证:文档评审通过

## 5. Consolidation 侧

- [ ] 5.1 UPDATE/SUPERSEDE 应用路径刷新 `last_confirmed_at`(SUPERSEDE 刷新 successor);验证单测:两条路径后锚点更新、检索不触碰锚点
- [ ] 5.2 planning prompt 的 importance 输出改为锚定分档语言并 clamp [0,1],缺省中性 0.5;验证单测:缺 importance 的候选落 0.5
- [ ] 5.3 触碰行时计算离线价值分(确认新鲜度 + 唯一会话热度对数压缩,分量与总分落库),不驱动任何删除;验证单测:分量可查、无驱逐副作用

## 6. Prefetch 钉住

- [ ] 6.1 prefetch 处理器改为会话开始融合检索一次并钉住记忆集,consolidation 完成刷新,刷新失败保留旧集;验证:集成测试连续两轮注入内容一致,consolidation 完成后下一轮变化,失败路径不阻塞对话

## 7. 存量回填

- [ ] 7.1 幂等回填脚本:分批限速对 mock 向量行重编码真实向量并清标记;验证:测试库跑批后无待向量化残留,重跑无副作用

## 8. 全量验证

- [ ] 8.1 `ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/` 全部 0 错误
- [ ] 8.2 `python -m pytest tests/ -q` 全量通过,含本变更新增的检索排序、降级、锚点、prefetch 钉住用例
- [ ] 8.3 手动验证 `MEMORY_FUSION_BIAS_ENABLED` 开关两态下 `memory_search` 排序与 breakdown 行为符合 spec 场景
