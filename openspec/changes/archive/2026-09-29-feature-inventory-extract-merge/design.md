# Design

## Context

`scripts/feature_inventory.py`(257 行,纯 stdlib + PyYAML)当前三个入口:`extract_entries(catalog_path)`(纯解析,已可注入路径)、`cmd_extract`(全量重建写入 `INVENTORY_PATH`,G5 破坏点)、`cmd_check`/`check_inventory`(ID 集合比对 + 治理规则)。`_GOVERNANCE_FIELDS`(:41)是未接线的死常量。CI 在 docs-check.yml:49 运行 `check`(非严格)。当前仓库 347 条,8 条 `research-candidate` 精化(catalog planned / YAML research-candidate),两向矛盾为零(已核验)。

## Goals / Non-Goals

**Goals**:extract 语义改为按 ID 非破坏合并;所有权规则成文(脚本 + YAML 头注释);矛盾显式失败进 extract 与 check;门槛要求的四类确定性测试(元数据不丢失/矛盾失败/幂等/状态一致)。

**Non-Goals**:受管表格生成与逐字段漂移检测、`responsibility`/`implementation_mode` 等新字段迁移、roadmap 受管区域(step2 主 change);不改 catalog/YAML 的现有内容;不改 CI 配置;不更新方案文档 G5 门槛行注记(随 step2 文档对齐)。

## Decisions

### D1: 合并算法 = 保留既有条目对象,只刷新索引镜像

按 ID 建既有条目映射;catalog 解析结果逐条合并——新 ID 追加 seed 条目,既有 ID 以 catalog 解析值为源刷新 `title/phase/category`,其余键(含未知扩展键)原样保留;YAML 独有 ID 保留并报告。实现为纯函数 `merge_entries(catalog_entries, existing) -> (merged, report, contradictions)`,不触碰文件系统,便于测试。排序:既有条目保持原顺序,新 ID 追加在尾部——保证幂等(输出顺序仅由首次出现顺序决定)。

*备选*:深合并或按字段白名单重建。否决——白名单会静默丢弃未知扩展键(step2 即将增加 `responsibility` 等字段,保留对象是前向兼容的正确默认)。

### D2: 矛盾判定 = ✅ XOR delivered,双向

`catalog_delivered = "✅" in title_cell` 与 `yaml_status == "delivered"` 不一致即为矛盾(两向都报)。合法空间:catalog 无 ✅ + YAML 非 delivered(含 8 条 research-candidate)。规则同时落入 extract(失败不写)与 check(ERROR)。落地为共享谓词 `_delivery_contradiction(catalog_delivered, yaml_status)`,两处调用同一实现。

*备选*:仅报单向(✅→非 delivered)。否决——反向(catalog 丢 ✅)同样是需要人工裁决的交付状态不一致,单检会漏。

### D3: 路径注入而非 monkeypatch

`cmd_extract/cmd_check` 增加可选 `catalog_path/inventory_path` 参数(默认仍为模块常量),argparse 不变;测试用 tmp_path 合成小 catalog/YAML 直接调用函数,不改仓库文件、不 monkeypatch 全局。符合 tests/AGENTS.md 的轻量 stub 风格。

### D4: YAML 头注释与摘要输出

写文件时在 yaml dump 前置固定头注释块(生成工具、所有权规则、矛盾处置指引);`--json-summary` 扩展为 `{count, delivered, new, refreshed, removed_kept}`,人类可读输出同步报告合并统计。schema 键保持 `"schema": 1` 不变(本 change 不升 schema,新字段属 step2)。

### D5: 死常量 `_GOVERNANCE_FIELDS` 接线为 seed 定义

用它定义首导 seed 的治理字段(maturity=None、dependencies=[]、evidence=None、acceptance=None),使所有权清单在代码中单点成文;`extract_entries` 的内联 seed 改为引用它。

## Risks / Trade-offs

- [矛盾规则未来与 step2 新状态值冲突(如 delivered 拆分技术预览/生产)] → 谓词单点实现 + step2 字段迁移时一并调整;规则文本注明以"delivered 语义"为界。
- [extract 刷新 title 会覆盖 YAML 中对 title 的手工修改] → 所有权模型明确 title 归 catalog;此类修改属反模式,报告行会暴露变更。
- [check 新规则在 CI 上遇到未来无意的状态漂移导致 docs-check 失败] → 这是期望行为(显式暴露),修复路径清晰(改 catalog ✅ 或改 YAML status)。

## Migration Plan

单 PR 纯工具变更:合入即生效,无数据迁移(合并逻辑保证既有 YAML 原样)。回退 = revert。合入后首次运行 extract 即验证非破坏性(diff 应仅出现头注释)。

## Open Questions

(无——矛盾方向、所有权边界、幂等语义均已按 G5 门槛文本与现状核验固定。)
