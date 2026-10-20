# Verification

## 基点与范围

基点 `78515965`（#290），分支 `feat/step10-evaluation-dataset-version-read-access-boundary`。本次在用户指定 worktree 审查和修正，未提交、未推送，未修改其他 worktree。

实际功能改动限定在 `src/hecate/ops/api/dataset_versions.py`、`tests/test_api/test_evaluation_dataset_version_access_boundary.py` 和本 change artifacts。入场时已有 `.agents/skills/openspec-propose/SKILL.md` 无关差异，本次保持原样；提交功能 change 时应排除该文件。

## 审查发现与修正

- diff 原先在验证源版本后立即检查 viewer，尚未验证 `against`。无角色调用方以不可见版本为目标时提前得到 403，违反全部归属先于角色的要求。新增双侧矩阵在修正前实际得到 **4 failed、12 passed**，四个失败分别为缺失、软删、跨 Dataset、跨 workspace 的目标。
- UUID 目标现在先经过读取 helper 验证父 Dataset、workspace 及路径绑定，再检查 viewer；`against=live` 复用已验证的父 Dataset。service 计算时的 not-found 仍被映射为恒定的 Dataset/Version 404，不回显资源 ID。
- 补齐缺失/软删父 Dataset 在三个 GET 的优先拒绝、system 跨 workspace 对两个可见版本的 diff，以及所有目标可见但无角色时的 403。
- 原只读测试仅比较版本行数，现比较 Dataset、Version、Item 的全字段快照，覆盖成功读取、403 和 404。
- 校正规划中残留的角色优先顺序、service 错误映射、首批 API 测试及“成功响应无 UUID”等不准确描述；成功响应保留原标识字段，失败响应不回显标识。

## 验证结果

Python 均使用本 worktree `.venv/Scripts/python.exe`。

| 检查 | 命令 | 结果 |
|---|---|---|
| 缺陷复现 | `python -m pytest tests/test_api/test_evaluation_dataset_version_access_boundary.py -k both_version_ownerships -q --tb=short`（修正前） | 4 failed、12 passed、21 deselected |
| 修正后边界测试 | `python -m pytest tests/test_api/test_evaluation_dataset_version_access_boundary.py -q` | 37 passed，0 failed，0 skipped |
| 完整 API/评测 service 回归 | `python scripts/dev.py test tests/test_api tests/test_services/test_evaluation -q -n 4 --tb=short` | 1063 passed，8 skipped，0 failed，19 warnings |
| 共享检查 | `python scripts/dev.py check all`（与 `bash scripts/dev.sh check all` 同一入口） | ruff check/format 通过，1616 文件；mypy 959 源文件无错误 |
| OpenSpec | `openspec validate step10-evaluation-dataset-version-read-access-boundary --strict` | valid |
| 规划数据 | `python scripts/feature_inventory.py check` | 413 entries，0 errors，189 warnings |
| 最终差异 | `git diff --check` | 通过 |
| 写路径范围核对 | 对比 HEAD 与当前源文件中四个函数的 `ast.dump` | create/delete/checkout 及 `_get_version_or_404` 全部一致 |

完整回归的 8 个跳过均为仓库显式标记：4 个 Schedule API 占位测试要求 PostgreSQL advisory locks，4 个 Security audit API 测试因 async_session_factory 单例依赖 PostgreSQL 而无条件跳过。这些用例未修改，不计作通过；此次读取边界的 37 项无跳过。

完整回归的 19 个 warnings 为 FastAPI 422 常量和 lark_oapi 所带 datetime 转换的弃用提示。边界测试的 warning 为既有 `HTTP_422_UNPROCESSABLE_ENTITY` 常量的弃用提示，来自非法 against 的 422 路径；此次保持其响应语义，不修改常量。规划数据的 warnings 为显式登记的治理字段及 evidence/acceptance 缺口，feature 数据文件未改变。

## 验收覆盖

- 父 Dataset 不存在、已删除或跨 workspace，三个版本读取入口均拒绝；版本不存在、已删除、跨 workspace、跨 Dataset 均为恒定的版本 404。
- diff 源/目标 × 四种不可见条件 × viewer/无角色矩阵；任一目标不可见时优先 404，全部可见但缺角色时 403。
- viewer/editor/admin 成功、显式 system scope 跨 workspace、缺 workspace 普通身份拒绝，不通过 None 获得全局读取。
- 可见空列表 200、分页 total、版本内容 hash、against=live/另一个版本/自身的 diff 及非法 against 422 保持。
- Dataset、Version、Item 在成功/拒绝读取前后全字段一致。

## 保留边界

- 写端点、service、ORM、数据库 schema、前端、Runtime 均未改变；此次不代表整个评测域的写授权已经闭合。
- 本地使用共享 SQLite/ASGI fixtures 验证 HTTP 边界；未运行真实 PostgreSQL 端到端、前端手动冒烟或 CI。完整仓库 pytest 仍由后续 CI 执行。
