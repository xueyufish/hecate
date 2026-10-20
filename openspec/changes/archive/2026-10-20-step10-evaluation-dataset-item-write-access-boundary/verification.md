# 复审验证

## 发现与修正

- 原实现只有 API 显式传入 workspace 时才能使新增样本正确归属。现有合成服务调用 `add_items(dataset_id, items)` 仍写零 UUID。新增 service 回归在修正前实测失败:实际零 UUID 与父 Dataset workspace 不符。修正为直接复用已查询的父 Dataset workspace,保持原 service 签名,不接受调用方指定 workspace,不回填历史样本。
- 原拒绝副作用快照使用 ORM 对象,可能读取 identity map 缓存,且未覆盖 metadata、generated_answer、deleted_at、时间戳。改为直接查询表的全部持久列,补充 Dataset/item 全表快照,避免 API session 的写入被缓存掩盖。
- 补充 viewer/无角色下父 Dataset 归属优先、PATCH/DELETE 的 item 绑定优先,覆盖缺失、软删除、跨 Dataset 与跨 workspace 样本的恒定无 ID 404;补充 system DELETE、新行归属、精确标记人和清除 provenance 断言。
- 修正文档:前序 change 只闭合读取边界,不能宣称本 change 闭合全部评测写入口;复用既有 `_get_item_or_404`,未新增 `_item_not_found`。

## 已执行验证

在本 worktree 使用 `.venv/Scripts/python.exe` 执行:

- 修正前: `-m pytest tests/test_services/test_evaluation/test_dataset_service.py::TestAddItems::test_items_inherit_parent_workspace -q --tb=short` → **1 failed**,确认缺陷。
- 修正后: `-m pytest tests/test_api/test_evaluation_dataset_item_write_boundary.py tests/test_api/test_evaluation_api.py tests/test_api/test_evaluation_dataset_access_boundary.py tests/test_services/test_evaluation/test_dataset_service.py -q --tb=short` → **71 passed**,2 个 FastAPI 422 常量弃用警告。
- `openspec validate step10-evaluation-dataset-item-write-access-boundary --strict` → 通过。
- `scripts/feature_inventory.py check` → **413 entries,0 errors,189 warnings**。warnings 为现有显式治理债务/待补 evidence 和 acceptance,本 change 未修改目录数据。
- AST 与 HEAD 比对: `evaluation.py` 仅 `add_items`/`update_item`/`delete_item` 改变,仅新增 `_get_item_for_write`;service 仅 `add_items` 改变。读取 helper、Dataset CRUD、Run 触发、版本接口及 Runtime 未改变。
- `git diff --check` → 通过。

- `-m pytest tests/test_api tests/test_services/test_evaluation -q -n 4 --tb=short` → **1088 passed,8 skipped,20 warnings**,0 failed。8 项跳过为仓库显式标记的 Schedule API/PostgreSQL advisory locks 与 Security audit API/PostgreSQL session singleton 用例;弃用警告来自 FastAPI 422 常量与 lark_oapi 的 UTC 时间转换。
- 最后补强 403 完整响应 envelope 的无资源 ID 断言后,`-m pytest tests/test_api/test_evaluation_dataset_item_write_boundary.py -q --tb=short` → **24 passed**,1 个上述 FastAPI 弃用警告。该文件 Ruff check 和 format check 再次通过。
- `scripts/dev.py check all` → **通过**:全仓共享 Ruff check、format check(1619 files)、mypy(src/packages/scripts/dev.py,960 source files)均无错误。

## 范围与限制

- 复审基于 worktree 的未提交实现和 HEAD `b3d9ab3a`。本地 `origin/main` 后续提交为 `4f97d289` 的 Runtime 变更;未执行 rebase、commit 或 push。
- worktree 原有 `.agents/skills/openspec-propose/SKILL.md` 修改与本 change 无关,复审保留未动,应从本功能提交中排除。
- 仓库完整测试与平台矩阵仍由 CI 执行;本地验证范围见上方实际命令。
