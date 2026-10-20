# Tasks

## 1. 读取边界实现(`src/hecate/ops/api/evaluation.py`)

- [x] 1.1 新增 `_dataset_not_found()`(与 `_run_not_found()` 同形的一致 404 envelope,不回显 ID)与 `_get_dataset_for_read(ctx, dataset_id, db)`(归属 + `~deleted` 校验,缺失/软删/跨 workspace 统一 404;不改动被写接口共享的 `_get_dataset_or_404`)。验证:模块导入成功,既有写路径调用点零改动(`git diff` 不含 `_get_dataset_or_404` 定义变化)。
- [x] 1.2 `GET /datasets/{dataset_id}` 与 `GET /datasets/{dataset_id}/items` 改用 `_get_dataset_for_read` + `ensure_workspace_role(ctx, WorkspaceRole.VIEWER)` → `_forbidden`;items 的筛选/分页调用保持原样。验证:两端口对缺失/软删/跨 workspace 返回同形 404,可见 dataset 行为不变。
- [x] 1.3 `GET /datasets` 按作用域过滤:非 system 缺 active workspace 在 service 调用前拒绝;system scope 不传过滤;其余传 `ctx.workspace_id` 给 `svc.list_datasets`。验证:列表条目与 `total` 仅含本 workspace,system scope 保持全局语义。

## 2. 边界测试(新增 `tests/test_api/test_evaluation_dataset_access_boundary.py`)

- [x] 2.1 镜像 `test_evaluation_run_access_boundary.py` 的夹具模式,覆盖:双 workspace 列表隔离(total 正确)、detail 一致 404(缺失/软删/跨 workspace 同体,不回显 ID)、items 一致 404、viewer 可读、归属优先于角色(跨 workspace 无角色 → 404 非 403)、本 workspace 无角色 → 403、system scope 跨 workspace、缺 workspace 非 system 不退化为全局、拒绝请求零写入、可见 dataset 空/筛选/分页语义不变。验证:新文件全部通过。
- [x] 2.2 按 `test_evaluation_run_access_boundary.py` 的既有模式验证拒绝路径不产生任何 dataset/item 写副作用。验证:断言在测试中显式成立。

## 3. 既有回归核对(`tests/test_api/test_evaluation_api.py`)

- [x] 3.1 已排查既有 dataset 用例，默认本 workspace admin 身份符合新边界，无需改动原成功断言；写路径用例断言保持。验证:`python -m pytest tests/test_api/test_evaluation_api.py tests/test_api/test_evaluation_dataset_access_boundary.py tests/test_api/test_evaluation_run_access_boundary.py -q` 全绿。

## 4. 回归与变更验证

- [x] 4.1 运行受影响面:`python -m pytest tests/test_api -q` 全绿;确认评测引擎/任务/报告等相邻套件无意外破坏。验证:命令输出 0 failed。
- [x] 4.2 运行 `openspec validate step10-evaluation-dataset-read-access-boundary --strict` 与共享检查(ruff check/format、mypy 于改动文件)。验证:全部通过,`git diff` 不含 service/ORM/schema 变更。


## 5. 审查补验

- [x] 5.1 将缺 workspace 身份的测试改为读取真实 Dataset/items，覆盖 JWT/workspace API key、缺角色及已有 viewer role；补齐 items 的 ownership-before-role、viewer/editor/admin 读取矩阵。
- [x] 5.2 补齐双 workspace 跨页精确 ID/total、排除软删除记录、越界空页及 system 全局读取的正对照；只读断言扩展为比较完整 Dataset/Item 列值。
- [x] 5.3 修正 delta spec 对 tags/known_bad 的 total 语义描述，补充未命中 tag 仍保留总数的回归，保持 service 与 API 实现不变。
- [x] 5.4 运行相关评测回归、API 套件、共享检查及 OpenSpec strict validation，将实际结果记录于 verification.md。

- [x] 5.5 修复完整 API 回归发现的 calibration 测试整点时钟不稳定：当前小时 +100 秒的人工评分可能处于未来，默认窗口排除后 pair_count 为 0。用 HEAD 原 API 和固定整点后 30 秒时钟复现，确认与本次读取边界无关；仅将该测试文件的数据锚定至上一小时，生产查询逻辑不变。
