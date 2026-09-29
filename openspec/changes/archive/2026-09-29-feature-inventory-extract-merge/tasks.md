# Tasks

## 1. 合并核心与路径注入

- [x] 1.1 重构 `scripts/feature_inventory.py`:新增纯函数 `merge_entries(catalog_entries, existing) -> (merged, report, contradictions)`(保留既有条目对象、刷新 title/phase/category、新 ID 尾部追加、YAML 独有 ID 保留)、共享谓词 `_delivery_contradiction`;`_GOVERNANCE_FIELDS` 接线为首导 seed;验证:函数无文件系统副作用,签名可通过 mypy
- [x] 1.2 `cmd_extract/cmd_check` 增加可选 `catalog_path/inventory_path` 参数(默认模块常量,argparse 行为不变);`cmd_extract` 改用 merge 结果写文件(矛盾时退出 1 且不写),前置所有权头注释,`--json-summary` 扩展 `{count, delivered, new, refreshed, removed_kept}`;验证:对仓库现状运行 extract,diff 仅出现头注释,`check` 输出与改动前一致
- [x] 1.3 `check_inventory` 接入矛盾检测(与 extract 共享谓词,ERROR 输出);验证:对仓库现状运行 `python scripts/feature_inventory.py check` 结果与改动前一致(0 errors、2 warnings)

## 2. 确定性测试(门槛关闭证据)

- [x] 2.1 新建 `tests/test_scripts/test_feature_inventory.py`(tmp_path 合成 catalog/YAML):元数据保留(治理字段 + 未知扩展键逐字段不变、索引镜像刷新有报告)、首导 seed、catalog 移除保留并报告;验证:pytest 通过
- [x] 2.2 矛盾用例(两向):extract 退出 1、报告含双取值、YAML 文件字节不变;合法精化(research-candidate)不触发;check 对矛盾报 ERROR、对现状干净状态输出不变;验证:pytest 通过
- [x] 2.3 幂等用例:同一输入连续两次 extract,产出字节相等;解析失败/重复 ID 仍响亮失败且不写文件;验证:pytest 通过

## 3. 验证与收尾

- [x] 3.1 本地四项门:`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`(src 无改动零差异)、`python -m pytest tests/test_scripts/ -q`;验证:全部通过
- [x] 3.2 真实数据冒烟:`python scripts/feature_inventory.py check`(0 errors)→ `extract` → `git diff docs/features/feature-inventory.yaml` 仅头注释 → 再 `extract` diff 为空 → `check` 仍 0 errors;验证:全程无手填字段丢失
- [x] 3.3 `openspec validate feature-inventory-extract-merge --strict` 通过;验证:输出无 error
