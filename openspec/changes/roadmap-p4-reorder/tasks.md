# Tasks: roadmap-p4-reorder

## 1. Catalog 调整 — 新增条目

- [x] 1.1 在 `docs/features/feature-catalog.md` 中插入 `1.3.20 Agent Versioning & Channel Publishing` 条目（位置：1.3.x 子族末尾，与 1.3.19 相邻；P4 段）
- [x] 1.2 在 1.3.20 条目 References 字段附「5.9d / 3.5.12 / 7.5」（承载三处悬空引用）

## 2. Catalog 调整 — 重复条目标记

- [x] 2.1 在 `1.3.10 Multi-Level Intent Recognition` 行 Description 末尾追加「⊕ Duplicate with 6.23; superseded by 6.23」指针
- [x] 2.2 在 `6.23 5-Level Intent Recognition` 行 Description 中前置补「Merges 1.3.10 (multi-level intent recognition).」

## 3. Catalog 调整 — 悬空引用修正

- [x] 3.1 在 `5.9d Skill Versioning` 行「Aligns with Resource Versioning (14.x) mechanism」旁追加「see 1.3.20 carrier」
- [x] 3.2 在 `3.5.12 Ontology Versioning` 行做同样追加

## 4. Roadmap 调整 — Sprint 8 Opening Queue

- [x] 4.1 在 Sprint 8 章节（`docs/features/roadmap.md:662+`）开头新增子节「Sprint 8 Opening Queue」（市场验证 + 底座 ✅ 4 项：5.4a / 7.2x 族 / 2.6a+1.1.21+1.3.10⊕6.23 族 / 1.3.20）
- [x] 4.2 把现有 Sprint 8 三块（Self-Learning / Agentic AI / Memory Intelligence）迁入新增子节「Absorption Pool」，保留原表格/编号/依赖
- [x] 4.3 把 Sprint 9 中 `2.6a` 与 `1.1.21` 两行移除（已迁 Sprint 8 Opening Queue）

## 5. Roadmap 调整 — M8 里程碑诚实标注

- [x] 5.1 在 M8 段落开头（`roadmap.md:706` 之前）加一行注释：6.20 / 6.22 的 closure 条件 = P5 KG integration 触发；不阻塞 Sprint 8 其他 Opening Queue 交付

## 6. Roadmap 调整 — Critical Path

- [x] 6.1 在 P4 Critical Path 段（`roadmap.md:1052+`）末尾追加 1.3.20 是 5.9d / 3.5.12 / 7.5 共同前置的注记

## 7. Verify

- [x] 7.1 `git grep "1.3.20"` 命中至少 3 处（catalog 新条目 + 5.9d/3.5.12 注 + roadmap 引用）
- [x] 7.2 `git grep "⊕ Duplicate"` 命中 1.3.10 行尾
- [x] 7.3 `git grep "Merges 1.3.10"` 命中 6.23 行
- [x] 7.4 `git grep "P5 KG integration"` 命中 6.20 / 6.22 / M8 段落
- [x] 7.5 `git diff --stat` 仅触及 `docs/features/{feature-catalog.md, roadmap.md}` 两个文件
- [x] 7.6 `openspec validate --change roadmap-p4-reorder` 通过

## 8. Commit & PR

- [ ] 8.1 单个 commit（type=docs，scope=features）；commit message 包含 `Refs:` 指向本 change
- [ ] 8.2 不 push；按仓库规范等待用户批准后由 `./scripts/opsx-flow.sh push` 处理

## 9. Model Management 补充（2026-09-06 追加，settings/models × AgentArts 对比）

- [x] 9.1 catalog 新增 `6.47 Model Service Publishing` 条目（Model Enhancement 小节，6.14 之后；复用 6.45 ✅ 机制）
- [x] 9.2 catalog 新增 `6.48 Model Management Quick Wins` 条目（同小节；搜索过滤 + 调用次数接入）
- [x] 9.3 catalog 6.13 行尾加防重复注记（list-level search pulled forward as 6.48）
- [x] 9.4 catalog 统计行同步：P4 148→150（25/150）、Total 389→391（195/391）
- [x] 9.5 roadmap Sprint 8 Absorption Pool 末尾新增「Model Management」小节（6.47/6.48）
- [x] 9.6 roadmap Sprint 8 reorder 注记、M8 验收清单追加 6.47/6.48 行
- [x] 9.7 roadmap M9 里程碑 96/96→98/98（102 features）、Milestone Summary 同步、统计表 P4 150/125、Total 391/196
- [x] 9.8 roadmap Critical Path 追加 6.45→6.47 与 traces→6.48 两条依赖链

## 10. 开发配置其余 5 子模块补充（2026-09-06 追加，实测路由策略/模型调测/意图管理/消息模板/对象管理）

- [x] 10.1 catalog 新增 `6.49 Intent Package Asset` 条目（AIP Enterprise Capabilities 小节，6.23 之后；意图分类+样例资产，供 6.23/2.6a few-shot）
- [x] 10.2 catalog 6.14 行加注记（路由策略作为可命名可绑定实体：模型组+总超时+重试，agent 绑策略而非单模型）
- [x] 10.3 catalog 6.7 行加注记（双模型并排对比调测；类型筛选由 6.11 ✅ 覆盖）
- [x] 10.4 catalog 14.2 行加注记（工作流异常通知消息模板资产化，低优按需）
- [x] 10.5 catalog 1.1.26 行加注记（轻量 schema 资产 Phase 1 / KG CRUD Phase 2 拆分建议）
- [x] 10.6 catalog 统计同步：P4 150→151（25/151）、Total 391→392（195/392）
- [x] 10.7 roadmap Opening Queue 控制器族行 + M8 验收行加 6.49
- [x] 10.8 roadmap M9 计数 98/98→99/99（103 features）、Milestone Summary 同步
- [x] 10.9 roadmap 统计表 P4 151/126、Total 392/197
- [x] 10.10 roadmap Critical Path 追加 6.23→6.49 依赖链
