# Tasks — step10-dataset-synthesis-access-boundary

## 1. service/job 层：种子校验与 pipeline 前移

- [x] 1.1 `DatasetSynthesisService` 新增 `SeedDatasetUnavailableError`（继承 `Exception`，非 `ValueError`）与 `validate_seed(seed_dataset_id, workspace_id)`：按 id + 未删除查询，缺失/软删/`workspace_id` 不符抛异常；`_load_seeds` 改为先 `validate_seed` 再按 `dataset_id` + 未删除读 item（不加 item.workspace_id 条件）。验证：service 层单测断言三类失效均抛异常、合法种子（含零 UUID workspace 的 item）可读。
- [x] 1.2 `synthesize()` 校验顺序前移：有 `seed_dataset_id` 时先通过 `_load_seeds` 验证并读取完整种子，再创建 target Dataset；确认种子失效异常不被 `except ValueError` 吞掉。验证：单测模拟种子失效，断言无 target Dataset 创建、异常类型正确。
- [x] 1.3 `_execute_job` 传播种子失效：`_run_pipeline` 抛 `SeedDatasetUnavailableError` 时 Job 经既有 `except` 路径 `_mark_failed`，error_message 明确指向种子不可用；同步路径由 API 层 catch 映射 404。验证：job 测试模拟排队后种子删除，断言 `status="failed"`、无 target Dataset、模型 stub 零调用。

## 2. API 层：synthesis.py 边界

- [x] 2.1 清理死代码：删除重复的 workspace 覆盖赋值块；`import asyncio` 移至顶部。验证：`python -m ruff check src/hecate/ops/api/synthesis.py` 零告警。
- [x] 2.2 新增 `_resolve_target_workspace(ctx, requested)`：非 system（缺 workspace → `NO_WORKSPACE`；foreign 值 → 恒定 FORBIDDEN；省略/相等 → 自身 workspace）；system（查询 `WorkspaceModel` 存在 + 未删除，不可解析 → `NO_WORKSPACE`）。POST 端点接入：解析目标 → 种子校验（catch `SeedDatasetUnavailableError` → `_dataset_not_found()`）→ `ensure_workspace_role(EDITOR)`（catch → `_forbidden`）→ 以验证后的目标 workspace 构造请求。验证：boundary 测试覆盖三类身份 × workspace 选择矩阵。
- [x] 2.3 GET 端点接入：非 system 缺 workspace → 查询前恒定 404；非 system 以 `ctx.workspace_id` 查询；system 以 `None` 跨租户查询；`get_job` 返回 None → 既有无 ID 404；可见后再 `ensure_workspace_role(VIEWER)`。验证：boundary 测试覆盖缺失/软删/跨 workspace/缺 workspace/角色矩阵。

## 3. 边界测试套件

- [x] 3.1 新增 `tests/test_api/test_synthesis_access_boundary.py`：双 workspace fixtures、角色矩阵（admin/editor/viewer/role-less/无 workspace/system）、完整错误 envelope（含 404 body 无任何标识符断言）、请求体 `workspace_id` 归属注入（foreign 值恒定 FORBIDDEN）、system 目标选择（存在/不存在/已删除）、种子隐藏（缺失/软删/跨目标 workspace，先于角色）、落库归属断言（Dataset/Item/Job 真实全列快照，无零 UUID）、拒绝路径零副作用（无 Dataset/Item/Job 行、后台 stub 零调度、模型 stub 零调用）、轮询不可见 job 统一 404、成功路径语义保持（200/202、响应结构、tags）。验证：`python -m pytest tests/test_api/test_synthesis_access_boundary.py -q` 全绿。
- [x] 3.2 既有回归：`python -m pytest tests/test_api/test_synthesis_api.py tests/test_services/test_evaluation/test_synthesis -q` 全绿，零修复或仅测试环境适配。

## 4. 全量验证与交付

- [x] 4.1 全量回归：`python -m pytest tests/ -q -n 4`（含真实 PostgreSQL 条件测试，如环境可用）；记录数字到 `openspec/changes/step10-dataset-synthesis-access-boundary/verification.md`。GLM 报告该次全仓执行有两个失败；本轮隔离复跑通过不等于全仓零失败。全仓及 PostgreSQL 矩阵仍待 CI，本地以受影响回归与共享检查为门禁。
- [x] 4.2 质量门禁：`python scripts/dev.py check all`（ruff check + format、mypy；此命令不运行 cz check）与 `git diff --check` 全绿；确认未触碰在途 Task 管理包文件（`evaluation_tasks.py` 等）。验证：命令零退出码。
