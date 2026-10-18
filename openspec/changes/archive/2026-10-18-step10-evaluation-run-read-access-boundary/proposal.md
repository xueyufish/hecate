# Proposal: step10-evaluation-run-read-access-boundary

## Why

评测 Run 只读查询接口缺少 workspace 与角色边界:任何已认证身份可以列出、读取、比较全部 workspace 的评测 Run 与分数。评测 Run 是 step10 治理证据链的组成部分(发布门禁消费评测证据),其查询授权边界属于演进方案 step10"证据查询授权边界"的未闭合切片;同类的 workspace 边界已在 step11(#283)为 Agent 版本生命周期交付,本切片将同一模式应用到评测查询面。

## What Changes

- `GET /api/evaluation/runs`:筛选(dataset_id/task_id)、计数与分页全部收窄到调用方 workspace;传入其他 workspace 的筛选值只能得到空结果,不得扩大范围。
- `GET /api/evaluation/runs/{run_id}`:先验证 Run 可见性(存在、未软删除、同 workspace),跨 workspace/已删除/不存在统一返回 404。
- `GET /api/evaluation/runs/{run_id}/scores`:读取前先验证 Run 可见性;**BREAKING** 对不存在/已删除/跨 workspace 的 Run 从"200 空列表"改为 404(当前实现连 Run 存在性都不校验,空/非空结果构成跨 workspace 存在性探测通道)。
- `POST /api/evaluation/runs/compare`:两侧 Run 均验证可见性与软删除;统一 404 detail 格式,消除当前"不存在"(回显 UUID)与"跨 workspace"(泛化文案)两种 404 可区分的存在性泄露;补齐读角色检查。
- 四个接口统一要求最低 `viewer` 角色(同 workspace);`system` scope API key 保持跨 workspace 可读;缺 workspace 的非 system 身份不得退化成全局查询。
- 成功响应结构与比较算法不变;所有接口保持纯读,不新增写入路径。

**已知相邻缺口(本 change 不处理)**:Dataset 端点存在同类问题——`list_datasets` 全局列出、`_get_dataset_or_404` 不校验 workspace(`create_run` 经它可跨 workspace 校验 dataset)。该缺口涉及写路径与 `EvaluationDatasetService`,留待独立切片;登记于此是为避免"评测读取边界已闭合"的误读。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `evaluation-api`:为 Run 列表、详情、分数查询与比较补充 workspace 可见性与最低角色要求;scores 对不可见 Run 的响应由空列表改为 404;compare 的 404 语义统一。

## Impact

- 代码:`src/hecate/ops/api/evaluation.py`(四个只读端点)。
- 测试:新增 `tests/test_api/test_evaluation_run_access_boundary.py`;回归既有 `tests/test_api/test_evaluation_*.py`、Workflow compare 与 Agent 发布评测相关测试。
- 规范:`openspec/specs/evaluation-api/spec.md` 经 delta 同步。
- 无数据库迁移、无前端改动、无新增依赖;复用 `AuthContext`、`ensure_workspace_role` 与 #283 建立的"先 ownership 后 role"边界模式。
