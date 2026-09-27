# Tasks: roadmap-structured-source

## 1. 结构化状态源（#13）

- [x] 1.1 新增 `scripts/feature_inventory.py`：`extract` 子命令（正则解析 feature-catalog.md 表格行 → YAML 骨架：id/title/phase/category/status（✅→delivered）；解析失败行 fail-loud 列表）与 `check` 子命令（重复 ID / 失效依赖 / 循环依赖 / production 缺 evidence / YAML↔目录 ID 集合漂移 = ERROR；evidence/acceptance 缺失 = WARNING；`--strict` 时 WARNING 升 ERROR）。确认 PyYAML 可用（缺失则入 dev extra）。验证：对当前目录运行 extract + check，输出合理。
- [x] 1.2 生成 `docs/features/feature-inventory.yaml`（extract 全量 + 六项拉前主线 11.16/11.17/13.1b/13.17/8.10/9.16a 的 dependencies 链与 evidence/acceptance + 七项降级的 research-candidate 标记手工校订）。验证：check 通过（非严格）。
- [x] 1.3 CI 挂载：`docs-check.yml` 增加一步运行 `python scripts/feature_inventory.py check`（按 docs-check 现有环境形态接入）。验证：本地模拟步骤命令通过。

## 2. 研究候选池（#15）

- [x] 2.1 `feature-catalog.md`：七项（6.15 / 6.38 / 6.39 / 2.12 / 6.20+6.22 完整闭环 / Global Branching / Embedded Ontology——最后两项正文行 apply 时定位）行尾加 `🔬 Research Candidate (2026-09-26)` 注记；新增 `## Research Candidate Pool` 区段：逐项四要素重启条件（真实场景 / 负责人 / 评估集 / 算力运维预算 + 现有工具不足说明）；6.39 重启形态注明"生成候选 → 沙箱验证 → 人工发布"；注明 6.20/6.22/1.1.26 的轻量 schema 用途不在降级范围。验证：`rg "Research Candidate" docs/features/feature-catalog.md` 七处行内注记 + 区段。
- [x] 2.2 `roadmap.md`：池指引一行（指向目录池区段）；P4/P5 汇总表行调整（6.15 移出 P4 清单；五项移出 P5 清单）+ 表下再分类注记（对齐 2026-08-22 先例格式，计数与行数一致）。验证：check 的汇总计数 WARNING 无新增。

## 3. Next Phase 主线（#14）

- [x] 3.1 `roadmap.md` 新增 `## Next Phase — Enterprise Hardening (P4′)` 区段：六项依赖链（11.16 → 11.17 → 13.1b → 13.17 → 8.10 → 9.16a）各一行（目标 + 复用资产 + 依赖 id），区段顶部声明由 feature-inventory.yaml 治理字段驱动。验证：check 校验 YAML dependencies 链与区段一致。

## 4. 门禁

- [x] 4.1 `ruff check scripts/feature_inventory.py`、`ruff format --check`、`mypy scripts/feature_inventory.py`（脚本入 ruff/mypy 面）。验证：0 错误。
- [x] 4.2 受影响验证：`python scripts/feature_inventory.py check`、docs-check 步骤命令、相关 docs 链接抽查。验证：0 错误。
- [x] 4.3 `openspec validate roadmap-structured-source` 通过。
