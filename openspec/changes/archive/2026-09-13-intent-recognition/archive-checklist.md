# Archive checklist — intent-recognition

> `/opsx-archive` 时逐项执行;完成后本文件随 change 一起归档留存。

## 1. docs/features/feature-catalog.md

- [ ] `6.23` 行:状态加 ✅,描述前缀补 "Shipped in `intent-recognition` (merges 1.3.10). L5 meta intent is a deterministic policy boundary, not a recognizer." 参考 AgentCore Routing Classifier / plan≠execute 的出处已在调研备注。
- [ ] `1.3.10` 行:确认 ⊕ 合并标记已存在(无需改;若描述仍含 "see Sprint 8 Opening Queue" 字样,更新为已交付)。
- [ ] `6.49` 行:状态加 ✅;**Depends on** 6.23, 2.6a → 6.23 已交付,2.6a 同 change 交付,改写为 "Shipped with 6.23 in `intent-recognition`"。
- [ ] `2.6a` 行:状态加 ✅(CONTROLLER 节点 + start/default/end + 漂移重路由)。
- [ ] `1.1.21` 行:状态加 ✅(画布 CONTROLLER 节点 + 映射编辑器 + intent_mapping 边类型)。
- [ ] `2.7c` 行:描述补一句 "INTENT mode upgraded 2026-09 to delegate to the 6.23 engine (package-backed form); legacy behavior byte-identical."

## 2. docs/features/roadmap.md

- [ ] Sprint 8 Opening Queue 表(约 L681):`2.6a + 1.1.21 + 1.3.10⊕6.23 (+6.49)` 行加 ✅ + 日期 + archive 路径 `2026-09-XX-intent-recognition`。
- [ ] L755 Opening Queue shipped 汇总行:Controller Family 从 pending 改为 ✅(archive 路径)。
- [ ] L1124 技术链:`LLM → 5-Level Intent Recognition (6.23) → Controller Self-Evolution → Intent Caching` 加 ✅ 标注(6.23+缓存已交付;Self-Evolution 为数据通路已交付、自动闭环未做)。
- [ ] L1144 链:`Intent Recognition (6.23) → Intent Package Asset (6.49) → few-shot classification evidence` 加 ✅。

## 3. docs/design/positioning.md

- [ ] 特性描述(P1→P5 catalog)按 positioning.md 的写作风格补意图识别/意图包能力描述(注意:不得出现具体数字/日期作描述性标记,规则见 docs/design/writing-style.md)。

## 4. AGENTS.md 修剪(归档必查)

- [ ] 检查本 change 是否产生了需要沉淀的新规则 —— 无新增命令/流程;gotchas 已进 docs/gotchas.md。AGENTS.md 无需增长(保持不动即合规)。

## 5. 遗留项(归档信息里注明,不阻塞)

- 嵌入检索式 few-shot 选择(kNN top-k)— 未做,follow-up。
- Redis 决策缓存(多副本共享)— 未做,follow-up。
- Dify Annotation Reply 式人工钉死路由(manual override)— 未做,follow-up。
- 真正的 held-out 评测(识别证据排除评测样例)— v1 为自一致性评测,真 held-out 需 runtime 证据排除机制,follow-up。
- 元意图(L5)自动策略推荐 — 明确非目标。
- Open Questions 中的调参项(few-shot 预算 5/类、总 30;漂移阈值 2)需在首个真实意图包数据上校准。
