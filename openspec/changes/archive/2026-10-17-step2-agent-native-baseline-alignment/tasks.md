# Tasks

## 1. 架构决策记录（ADR）

- [x] 1.1 新增 `docs/design/adr/037-agent-native-execution-model.md`：固定 Harness、Agent Controller、动态 Plan、Workflow Executor/Pregel、共享持久化执行机制、可信动作执行六类职责与状态/组合边界（对齐演进方案第一节职责表）；包含 D1 的 supersedes 范围表（ADR-001/007/032/036 精确到决策点）与 ADR-032 code-as-plan 排除理由的复核记录；Status 标注"方向已接受，Controller 实现与验收未交付"。验证：文件存在且引用的演进方案章节、旧 ADR 编号全部可解析。
- [x] 1.2 更新旧 ADR 状态行：ADR-001/007/032 的 Status 各加一行 "Partially superseded by ADR-037 (see its scope table)"（正文与 Consequences 不改）；ADR-036 的 Status 段补一行指向 ADR-037，说明被替代的两个决策点、其余修复与证据继续有效；ADR-034/035 仅修正 Consequences/正文中的 step8 认证引用与"内置 Runtime = Pregel"表述。验证：`grep -n "ADR-037" docs/design/adr/` 命中全部目标文件；被替代决策点的原文未被删除。
- [x] 1.3 更新 `docs/design/adr/INDEX.md` 与 `docs/design/README.md` 的 ADR 时间线/主题索引，登记 ADR-037。验证：两处索引均含 ADR-037 条目且链接可解析。

## 2. 定位与架构文档

- [x] 2.1 更新 `docs/design/positioning.md`：strategic summary 与 delivery boundaries 中"内置 Runtime"的表述扩展为"自主 Agent Controller + 确定性 Workflow/Pregel 的一等执行模型"（Controller 标注待实现）；"Hosted Agent services are a first-class execution backend"段与对比矩阵 Engine/Execution extensibility 行改为"外部 Runtime 接入移出当前方案、已交付契约与试点保留"；M-A/I-Cb 与 I-Ca 的近期目标表述对齐演进方案第七节。验证：文档内无"收敛到 Pregel/外部后端认证属于当前承诺"的残留表述（`grep -n "external backends through versioned contracts" docs/design/positioning.md` 无当前承诺语义命中）。
- [x] 2.2 更新 `docs/design/engine-design.md`：Terminology 段区分图执行内核、Agent Controller（待实现）与共享持久操作的目标职责，标注"当前实现 = 共享装配 + Pregel 图 + 直连循环，Controller 未交付"。验证：新增段落明确含"待实现/未交付"标注。
- [x] 2.3 更新 `docs/design/architecture.md` 的 Control Plane/Execution 相关段落与 `packages/hecate-runtime/AGENTS.md` 术语段：登记目标逻辑边界（Controller/Harness/共享持久操作），保留自足约束与 sanctioned imports 清单不动。验证：`git diff` 确认 Runtime AGENTS.md 的 self-sufficiency 段与清单零变更。

## 3. 规范同步（delta specs 已随本 change 提交）

- [x] 3.1 核对 delta specs 与主 specs 的一致性：`unified-chat-execution` delta 的 MODIFIED requirement 与主 spec 第一个 requirement 除限定语外逐句一致（7 场景原文保留）；`dynamic-orchestration` delta 未改动任何既有 requirement。验证：`openspec validate step2-agent-native-baseline-alignment --strict` 通过。

## 4. 规划数据源与生成文档

- [x] 4.1 更新 `scripts/feature_inventory_tables.yaml`：iterations 的 I-D 行改为已撤销（保留编号，scope 说明外部 Runtime 接入移出方案、重启需新方案，无交付物）；milestones 的 M-A 行 Covers 改为 step1—7、exit conditions 聚焦内置受管闭环（I-Cb）并删除 hosted harness/heterogeneous 表述；contract_registry Runtime 行去掉 "frozen at step8"（改为按能力独立冻结）。验证：YAML 解析通过、无 step8/hosted harness 当前承诺残留。
- [x] 4.2 更新 `scripts/feature_inventory_plan.yaml`：review-01 的 `step: 5/8/19` → `5/19`，milestone/acceptance 文本同步；全文件 `grep -n "step8\|/8/\|8/19" ` 核对其余映射行无过期 step8 引用。验证：grep 结果仅剩历史性引用（如有）且逐一标注保留理由。
- [x] 4.3 如需在 `docs/features/feature-inventory.yaml` 登记 Agent Controller 待实现条目，先核对现有 Feature ID 占用再追加未使用后缀（不重编号、不改既有条目 status/evidence/maturity）。验证：`python scripts/feature_inventory.py check` 通过、人工字段零丢失。
- [x] 4.4 运行 `python scripts/feature_inventory.py sync` 再生成 `feature-catalog.md` 与 `roadmap.md` 受管区域，然后二次运行验证幂等（零 diff）。验证：`git diff docs/features/` 仅含对齐演进方案的预期行（I-Ca/I-Cb/I-D/I-F、M-S/M-A/M-C、contract_registry/plan-mappings 及新条目）；`feature_inventory.py check` 零漂移。

## 5. 验证与收尾

- [x] 5.1 运行规划工具相关测试：`python -m pytest tests/ -q -k "feature_inventory"`（或定位实际测试文件后运行对应目录）。验证：相关测试全部通过；如存在失败先调查是否为本 change 引入。
- [x] 5.2 运行适用共享检查：`bash scripts/dev.sh check all` 中与本 change 相关的部分（ruff/mypy 预期无影响，因无 `src/` 变更；如 `check` 包含文档链接校验则一并执行）。验证：0 错误或明确记录不适用项。
- [x] 5.3 汇总验证结果到本 change 的 `verification.md`（如仓库惯例要求）或 PR 描述：列出 check/sync 幂等/validate/测试的实际输出要点、未完成项与保留限制。验证：PR 描述含实际验证结果而非预期结果。

## 6. 复核修正

- [x] 6.1 对齐遗漏的 I-Ca/I-Cb/I-F 与 M-S/M-C 门槛，清除当前 catalog 中 step8 认证承诺；保留历史状态与证据。
- [x] 6.2 将新 Controller 条目映射到一致的 core-04a；撤销旧外部 Runtime 条目的当前 M-A 归属，补齐增量准入所需的未核验元数据。
- [x] 6.3 修正 ADR 中未获实测支持的依据、code-as-plan 排除理由和状态 owner；delta 显式提供归档时的目标 Purpose，新增恢复能力保持待交付。
- [x] 6.4 运行严格增量准入、生成幂等、OpenSpec 校验、相关测试及共享检查，并比较元数据保留与修改范围，将实际结果写入 review.md。
