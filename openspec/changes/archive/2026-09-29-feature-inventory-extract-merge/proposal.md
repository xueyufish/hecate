# Proposal

## Why

G5 门槛（方案 §二"实施门槛及基线处置"）:`scripts/feature_inventory.py` 的 `extract` 对 catalog 全量重建 YAML——已填写的 `maturity/dependencies/evidence/acceptance` 及任何手工维护字段被静默清零(`cmd_extract`,scripts/feature_inventory.py:206—227);`check` 只比对 ID 集合,不做字段级一致性检测(:172—177)。方案要求"先保护存量数据,改为按 ID 合并或仅首次导入;明确字段所有权;冲突显式报告,不静默覆盖",并规定"不得直接再运行当前 extract 覆盖已有清单"。step2(`governance-platform-positioning`)将频繁触碰 feature 清单,本修复是其安全前置(方案 §七首轮拆分表:"G5 工具修复单独 PR")。CI 已在 docs-check.yml:49 运行 `check`,extract 的破坏性同样威胁任何误操作。

## What Changes

- **extract 由全量重建改为按 ID 合并**(非破坏性):
  - 既有条目:保留 YAML 拥有的字段(`status`、`maturity`、`dependencies`、`evidence`、`acceptance` 及任何未知扩展键),仅刷新 catalog 派生的索引镜像字段(`title`/`phase`/`category`)并在输出中报告变更;不删除 catalog 中已移除的 ID,登记报告后保留(由 `check` 的既有 ID 漂移错误持续暴露,直到人工处置)。
  - 新 ID:首次导入,seed 规则不变(状态取自 catalog ✅,治理字段置空)。
  - **矛盾显式失败**:catalog 标 ✅ 而 YAML 非 delivered,或 catalog 无 ✅ 而 YAML 为 delivered——两个方向都意味两份文件对"是否已交付"不一致,必须人工裁决;extract 失败且不写文件。当前仓库 347 条已核验无矛盾(8 条 `research-candidate` 精化为合法差异,不触发)。
- **字段所有权成文**:catalog 拥有索引镜像(title/phase/category),YAML 拥有机器可读状态与治理数据(status/maturity/dependencies/evidence/acceptance);写入脚本 docstring 与生成的 YAML 头注释。
- **`check` 增加同规则矛盾检测**(catalog ✅ 与 YAML delivered 的两个矛盾方向报 ERROR);ID 集合漂移检测保持不变;非严格模式对缺证据的 WARNING 行为不变,CI 现状零影响。
- 新增确定性测试覆盖门槛关闭证据:提取前后元数据不丢失、矛盾冲突失败(两个方向)、生成幂等(两次运行字节一致)、状态一致(catalog/YAML 精化合法、矛盾报错)、首导 seed、catalog 移除保留、解析失败响亮。
- 纯工具与测试资产改动;不改 `feature-catalog.md`、`feature-inventory.yaml` 内容与 CI 配置。

## Capabilities

### New Capabilities

- `feature-inventory-governance`:结构化功能清单的提取/合并契约——字段所有权(catalog 索引镜像 vs YAML 权威数据)、extract 的按 ID 非破坏合并与显式矛盾失败、check 的矛盾检测、生成幂等性。

### Modified Capabilities

(无——仓库不存在 feature inventory 相关 spec(已核验 `openspec list --specs`),本 change 新建该能力。)

## Impact

- **修改**:`scripts/feature_inventory.py`(merge 逻辑、矛盾检测、路径参数化以便测试、docstring;`_GOVERNANCE_FIELDS` 死常量接线或移除)。
- **新增**:`tests/test_scripts/test_feature_inventory.py`(tmp_path 合成 catalog/YAML,不触碰仓库文件);生成的 YAML 头注释。
- **CI**:docs-check.yml 的 `check` 行为不变(矛盾规则在当前 347 条上零命中,已核验);新增测试随 pytest 运行。
- **不做**(留给 step2 主 change):受管表格生成与逐字段漂移检测、`responsibility`/`implementation_mode` 等新字段迁移、roadmap 受管区域、G5 门槛行注记更新。
