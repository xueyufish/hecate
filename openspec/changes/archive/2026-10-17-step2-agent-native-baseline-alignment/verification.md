# 验证记录 — step2-agent-native-baseline-alignment

本 change 仅涉及文档、OpenSpec 工件与规划数据，不改变任何运行时代码、schema、路由或开关。以下保留 GLM 实施会话的验证记录。后续复核发现普通 check 未覆盖严格增量准入，以及规划同步遗漏；已修正并复跑，最终结论与实际结果以 [review.md](review.md) 为准。

## 静态门

- `bash scripts/dev.sh check all`：ruff check 0 错误；ruff format --check 1578 文件已格式化；mypy `src/ packages/ scripts/dev.py` 933 源文件零错误（本 change 无 Python 代码变更，属预期通过）。
- `openspec validate step2-agent-native-baseline-alignment`：valid。

## 规划数据工具链（实际输出）

- `python scripts/feature_inventory.py check`：`check ok: 413 entries, 0 errors, 191 warnings`。191 条 warnings 为既有显式债务（5.4a/5.9-enh 等缺治理字段、184 条缺 evidence、187 条缺 acceptance 的历史回填项），与本 change 无关且数量与变更前一致；首次运行报出的 3 条 DRIFT（roadmap I-D 行）经 `sync` 后消除，复跑无 DRIFT。
- `python scripts/feature_inventory.py sync`：catalog 4 个受管区 + roadmap 3 个受管区再生成；二次运行输出 `already in sync` 且 `git diff --stat` 零变化（幂等验证通过）。
- 受管区 diff 逐行核对：仅含预期变更——I-D 行改为已撤销（保留编号）、M-A 行改为"Built-in managed governance loop"（step1—7，退出条件聚焦 I-Cb）、contract_registry Runtime 行去掉 "frozen at step8"、全部 step8 锚点移除、新增 1.3.11l 行（主表/dispositions/plan-mappings 三处）。
- `python scripts/feature_inventory.py check` 于 sync 后复跑：0 errors、无 DRIFT；当时只核验了 catalog↔YAML 一致性；历史字段保留另经复核的逐字段基线比较确认（见 review.md）。

## 测试

- `python -m pytest tests/test_scripts/test_feature_inventory.py -q`：**62 passed**（7.50s），覆盖 extract/check/sync 行为与受管区生成。

## delta spec 一致性核对（程序化 diff）

- `unified-chat-execution` 的 MODIFIED requirement 与主 spec 原文逐行 diff：仅两处预期差异（"Pregel chat 图" → "现行引擎路径(Pregel chat 图)" 限定语；新增一句兼容路径/收敛目标指向），7 个 Scenario 原文保留，主/边两侧场景数一致（7/7）。
- `dynamic-orchestration` delta 全部为 ADDED Requirements，未触碰主 spec 任何既有 requirement（按构造验证）。

## 实施中发现并处理

1. **worktree 引导覆盖仓库定制 skill**：`opsx-flow.sh start` 的 OpenSpec setup 用上游通用版刷新了 `.agents/skills/openspec-propose/SKILL.md`，会撤销 #263 引入的 Hecate 轻量分层选择逻辑。已 `git checkout` 还原该文件，不进入本 change（后续 worktree 启动需注意此副作用）。
2. **step8 引用全量清点**：任务书最初只覆盖 tables.yaml/plan.yaml 的显式行；实际 `feature-inventory.yaml` 中另有 46 处 step8 步骤锚点（step5/8/19 ×20、step3/8/11/16 ×12 等）。已全部处置：批量移除失效锚点，ACP（2.13）等以 step8 为唯一锚点的条目标注 "revoked/on-revival (ADR-037)"，与 delta/roadmap 的撤销语义一致。
3. **README ADR 索引缺口**：`docs/design/README.md` 的 ADR 时间线只登记到 ADR-032（033–036 为既有缺口）。新增 "Platform Evolution (ADR 033–037)" 小节补齐 033–036 并登记 037。

## 未完成项与保留限制

- 主 specs 的 Purpose 段（`unified-chat-execution` 的"收敛到统一 Pregel 运行时"表述）归档同步时须按两个 delta 新增的 `## Purpose` 显式更新并再次核对；不能认为 MODIFIED/ADDED 要求会自动更新 Purpose。本 change 不直接改主 specs，尚未执行同步或归档。
- ADR-037 只授权方向与边界：Agent Controller 未交付、默认开关不翻转、入口不迁移；1.3.11l 登记为 `planned/unverified`，不构成任何能力已支持的声明。
- 191 条既有 inventory warnings（历史回填债务）未在本 change 处理，数量与变更前一致。
