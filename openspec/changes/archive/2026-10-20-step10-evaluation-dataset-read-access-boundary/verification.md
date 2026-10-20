# Verification

## 基点与范围

基点 `e8e52e1f`（#288），分支 `feat/step10-evaluation-dataset-read-access-boundary`。本次审查在用户指定 worktree 中进行，未提交、未推送，未修改其他 worktree。

入场已有 `.agents/skills/openspec-propose/SKILL.md` 的无关差异，本次保留原样；建议提交本 change 时排除该文件。

## 审查结论与修正

- 三个 Dataset 读取接口的实现符合预期：列表在 service 查询前检查 viewer role，再按当前 workspace 过滤；detail/items 先检查归属及删除状态，再检查 role；显式 system scope 可跨 workspace，缺 workspace 的普通身份不会退化为全局查询。API 实现未进一步修改。
- 用 HEAD 和工作文件的 AST 对比确认 `_get_dataset_or_404`、`_get_item_or_404` 及七个写端点原样保留。
- 新增测试原本只以不存在 ID 验证缺 workspace 身份，无法证明其无法读取真实 Dataset。改为包含私有条目的真实 Dataset，并覆盖 JWT/workspace API key、缺角色/缓存 viewer role。
- 加强双 workspace 的分页精确 ID、排序、total、耗尽页及软删除排除断言；system 测试使用两个 workspace 并校验精确 ID，补充 role-less items ownership、viewer/editor/admin 读取和完整行值只读断言。
- 修正 delta spec 对“筛选后准确 total”的错误承诺：tags 在分页后过滤且不缩减 total，known_bad 在 SQL 过滤后计数/分页。补验未命中 tag 返回空 items 但 total 仍为 3；未改 service。
- 完整 API 回归暴露 calibration 旧测试的整点时钟问题，已验证并修复测试数据锚点，保留全部统计断言和生产实现。

## 实际验证

所有 Python 命令使用该 worktree 的 `.venv/Scripts/python.exe`。

| 检查 | 命令/范围 | 结果 |
|---|---|---|
| 原边界测试 | `python -m pytest tests/test_api/test_evaluation_dataset_access_boundary.py -q`（补验前） | 11 passed |
| 加强后的边界测试 | 同上 | 17 passed |
| Dataset + annotations 定向回归 | `python -m pytest tests/test_api/test_evaluation_annotations_api.py tests/test_api/test_evaluation_dataset_access_boundary.py -q` | 29 passed |
| 完整 API + Dataset service 首跑 | 下方完整命令（未加 -rs） | 1 failed，792 passed，8 skipped；失败定位为 calibration 时钟数据，见下节 |
| 完整 API + Dataset service 复跑 | 下方完整命令 | 793 passed，8 skipped，0 failed，17 warnings |
| 共享检查 | `python scripts/dev.py check all`，与 `bash scripts/dev.sh check all` 同一实现 | ruff check/format 通过（1615 文件）；mypy 959 源文件无错误 |
| 时钟修正文件检查 | `python -m ruff check tests/test_api/test_evaluation_annotations_api.py`；`python -m ruff format --check tests/test_api/test_evaluation_annotations_api.py` | 通过 |
| OpenSpec | `openspec validate step10-evaluation-dataset-read-access-boundary --strict` | valid |
| 规划数据 | `python scripts/feature_inventory.py check` | 413 entries，0 errors，189 warnings |
| 差异格式 | `git diff --check` | 通过 |

完整回归命令：

```text
python scripts/dev.py test tests/test_api tests/test_services/test_evaluation/test_dataset_service.py -q -n 4 -rs
```

四个 pytest worker 各有独立的共享内存 SQLite 数据库，不连接真实 PostgreSQL。

## calibration 失败调查

失败用例：`tests/test_api/test_evaluation_annotations_api.py::TestCalibrationEndpoint::test_numeric_calibration_stats`。原测试将 `_NOW` 取为当前整点，人工评分时间为 `_NOW +100..109` 秒，而报表默认窗口结束于请求时刻；整点附近数据处于未来，因此 `pair_count` 为 0。

- 首次完整回归失败，单独普通时钟复跑 1 passed，证明存在时间依赖。
- 不修改磁盘源码，通过内存加载 `git show HEAD:src/hecate/ops/api/evaluation.py`，将报表时钟固定到整点后 30 秒：同一断言再次失败（0 != 10），证明未修改 API 的基线也受影响。
- 仅将测试文件的 `_NOW` 锚定到上一小时；以相同整点后 30 秒时钟再运行，1 passed。原 `pair_count=10`、`agreement_rate=0.9`、`mae=0.055` 和 heatmap 断言均保留。

## 验证边界

- Dataset 写接口、Run 创建、版本管理、service/ORM/schema 和前端未修改；本切片仅闭合三个指定读取入口，不代表评测域所有写路径授权已闭合。
- 原有 Dataset API 测试使用本 workspace admin 身份，其正常读取符合新边界，没有放宽或重写旧成功断言。
- 本地不运行全仓 pytest 或 CI；完整 API 范围按该 change 的任务执行。真实 PostgreSQL/生产宿主和手动前端冒烟未执行，本次没有相关实现或迁移变更。


## 最终跳过与警告说明

- 8 项条件跳过：4 项 Schedule API 要求 PostgreSQL advisory locks，4 项 Security audit API 要求 PostgreSQL 的 async_session_factory singleton。这些不属于本 change 的三个 Dataset 读取入口，不计为通过。
- 17 项 warning 来自既有 FastAPI 422 常量和第三方 protobuf UTC API 弃用提示，未涉及本次权限实现；没有为消除提示修改生产行为。
- 规划检查的 189 项 warning 为已显式登记的治理字段、evidence/acceptance 缺口；本次未修改 feature 数据文件。
