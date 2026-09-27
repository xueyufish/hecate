# Proposal: roadmap-structured-source

## Why

外部安全评审 P1 #13/#14/#15：规划可信度。

- **#13 状态源不可信**：`feature-catalog.md`（1069 行 markdown）与 `roadmap.md`（1186 行）是仅有的状态载体——前者人读尚可、机器不可查（重复 ID、失效引用无法检测），后者历史注记层层堆叠（新旧统计并存，自述" Prior figures drifted"）。完成状态不区分 experimental/beta/production，"接口存在"与"占位实现"无从区分。
- **#14 顺序失焦**：身份管线（11.16/11.17）、Agent Identity（13.1b）、环境与发布管理（13.17）、CI/CD 评估门禁（8.10）、多副本故障测试（9.16a）——直接支撑"企业级、多租户"定位的能力散落在 P4/P5 的 100+ 项里，没有一条"开发配置 → 固定版本 → 回归评估 → 审批 → 发布 → 回滚"的显式主线。
- **#15 承诺过宽**：Agentic RL、PDDL+MCTS 自规划、自动造工具、AP2 支付、完整 Ontology/OAG、Global Branching、Embedded Ontology——把 Hecate 拉向训练平台、支付基础设施或行业数据平台，且均无真实场景/负责人/评估集支撑。

（#15 清单按评审建议照单执行；PR review 中可调整，docs 变更随时可逆。）

## What Changes

- **#13 结构化状态源**：新增 `docs/features/feature-inventory.yaml`（每项 `id/status/maturity/phase/dependencies/evidence/acceptance`）与 `scripts/feature_inventory.py`（`extract`：从 feature-catalog.md 提取 id/名称/✅ 状态生成 YAML 骨架；`check`：重复 ID、失效依赖引用、循环依赖、production 缺 evidence、YAML↔目录 ID 集合不一致——CI 可执行）。状态语义：`planned / delivered ✅ / research-candidate / dropped` + 交付项的成熟度 `experimental / beta / production`（默认 experimental；production 必须有 evidence）。渐进迁移：本期 YAML 覆盖全部 id/status（脚本提取），evidence/acceptance 仅对拉前主线的六项 + 降级七项填写，其余允许为空（check 先警告后强制，分两阶段）。
- **#15 研究候选池**：feature-catalog.md 与 roadmap.md 各增 "Research Candidate Pool" 区段；七项（6.15 / 6.38 / 6.39 / 2.12 / 6.20+6.22 完整 Ontology-Actions+OAG 闭环 / Global Branching / Embedded Ontology）标注 `🔬 research-candidate` 并附重启条件（真实场景 + 负责人 + 评估集 + 算力/运维预算 + 现有工具为何不足）；6.39 如重启改为"生成候选 → 沙箱验证 → 人工发布"。原行保留并加池标记（不删行，避免 ID 引用断裂）；P4/P5 汇总表计数与清单同步调整（沿用 2026-08-22 再分类注记先例）。
- **#14 下一阶段主线**：roadmap.md 新增 "Next Phase — Enterprise Hardening (P4′)" 区段（由 YAML 的 dependencies 字段驱动生成）：11.16/11.17 身份管线 → 13.1b Agent Identity → 13.17 环境与发布管理 → 8.10 CI/CD 评估门禁 → 9.16a 多副本故障测试，交付"配置 → 固定版本 → 回归评估 → 审批 → 发布 → 回滚"一条路径；复用既有评估/版本/审批资产，不另建质量网关。历史区段不动。
- **CI 挂载**：`docs-check.yml`（或 ci.yml 轻量 job）执行 `feature_inventory.py check`。

## Capabilities

（skip_specs: true——规划/工具变更，无产品行为变化。）

## Impact

- **文件**：新增 `docs/features/feature-inventory.yaml`、`scripts/feature_inventory.py`；修改 `docs/features/feature-catalog.md`（七项标记 + 池区段 + 汇总表）、`docs/features/roadmap.md`（Next Phase 区段 + 池指引）、`.github/workflows/docs-check.yml`（check 步骤）。
- **风险**：文档 surgery 规模大（目录 1069 行）；以"原行保留 + 标记"策略避免断裂；脚本提取以正则对目录表格行，格式异常时 fail-loud。
- **无代码、无迁移、无 BREAKING**；回滚 = revert。
