# Proposal — step10-dataset-synthesis-access-boundary

## Why

数据集合成的三个入口目前完全游离在平台租户隔离授权体系之外，构成跨租户读写通道：

1. `POST /api/evaluation/datasets/synthesize` 接受请求体 `workspace_id` 任意覆盖资源归属——任何已认证身份可把合成出的 Dataset、Item、Job 写进任意 workspace（含不存在或已删除的 workspace，服务层 `workspace_id` 列上的历史零 UUID default 也因此可达）；
2. pipeline 的种子读取只按 `dataset_id` 过滤 item，不校验种子 Dataset 的存在、软删与归属，任何身份可读取其他租户的种子样本内容（跨租户数据外泄）；
3. `GET /api/evaluation/synthesis-jobs/{job_id}` 在 `workspace_id=None` 时取消租户过滤，任何无 workspace 身份可按 ID 轮询任意租户的 job 配置（含 `llm_config`、种子与目标数据集 ID）。

同批次的 step10 边界系列已覆盖 Dataset 读写（#290/#300）、版本（#293/#295）、样本（#296）、Task（#299）、分数（#303）；本 change 闭合同一租户隔离线上的合成域，保持既定边界惯例（ownership 先于 role、恒定无 ID 错误、`NO_WORKSPACE` 语义、system 跨租户豁免）。

## What Changes

- **`POST /api/evaluation/datasets/synthesize` 增加写边界（最低 EDITOR）**：
  - 非 system 身份必须有 active workspace（缺失 → 403 `NO_WORKSPACE`）；请求体 `workspace_id` 只能省略或等于自身 workspace，指定其他值一律恒定 403 FORBIDDEN（不回显目标 workspace 是否存在）。
  - system 身份保留既有 workspace 选择字段：可显式指定一个存在且未删除的目标 workspace；无可解析目标（未指定、或指定值不存在/已删除）→ 403 `NO_WORKSPACE`。
  - 目标 Dataset、Item、Job 一律以服务端验证后的目标 workspace 落库，杜绝零 UUID 归属。
- **种子 Dataset 归属校验（API 层，先于角色判断）**：提供 `seed_dataset_id` 时，种子必须存在、未删除且属于目标 workspace；缺失、软删、归属不符统一返回无 ID 的 Dataset 404。校验在任何目标数据、Job 行、后台任务、模型调用之前完成。样本归属以父 Dataset 为准，历史零 UUID 样本仍可作为合法种子。
- **pipeline 执行时二次校验种子**：后台执行（及同步执行的防御性复查）在创建目标 Dataset、读取样本、调用模型之前重新验证种子；排队后种子被删除/失效时 Job 显式失败（不回退 topic、不生成目标 Dataset、不调用模型）。
- **`GET /api/evaluation/synthesis-jobs/{job_id}` 增加读边界（最低 VIEWER）**：先判定 job 可见性（存在、未软删、属于调用方 workspace），再判角色；缺失、软删、跨 workspace 统一无 ID 404，非 system 缺 workspace 在查询前以同一 404 拒绝；system 可按 job ID 跨 workspace 轮询。
- 顺带清理 `synthesis.py` 中重复的死代码块（两段相同的 workspace 覆盖赋值）。

**不改变**：同步/异步分界（`count <= 20` → 200 / `> 20` → 202）、响应结构、生成/进化/对抗三种策略、过滤管线、provenance tags、`422` 参数校验、公开 schema 与共享 Dataset/auth helper 的签名语义。

**非目标**：DLP/模型出站治理、后台任务恢复机制重构、在途的 Task 管理写边界（另包进行）、公开模型/迁移/依赖/composition 变更。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `dataset-synthesis`: 新增合成访问边界 requirement——合成发起（身份/workspace 选择/角色/种子归属校验顺序）、pipeline 执行期种子复验、job 轮询读边界；现有 request shape、同步/异步、策略、过滤、tags、审计 requirement 语义不变。

## Impact

- `src/hecate/ops/api/synthesis.py` — 两个端点的边界逻辑（目标 workspace 解析、种子校验、角色闸门、404/403 恒定 envelope）+ 死代码清理。
- `src/hecate/ops/evaluation/synthesis/service.py` — `synthesize()` 内种子二次校验前移（先于目标 Dataset 创建）；种子失效的显式错误语义。
- `src/hecate/ops/evaluation/synthesis/job.py` — 视需要微调后台执行路径以传播种子失效错误（`get_job` 查询语义保持）。
- 复用既有共享 helper（只导入、不改签名）：`evaluation.py` 的 `_dataset_not_found`/`_no_workspace`/`_forbidden`，`core/deps_workspace.py` 的 `ensure_workspace_role`/`WorkspaceRole`/`WorkspaceRoleRequiredError`。
- 测试：新增 `tests/test_api/test_synthesis_access_boundary.py`；回归 `tests/test_api/test_synthesis_api.py`、`tests/test_services/test_evaluation/test_synthesis/`。
- 风险档位：full spec-driven（API 鉴权 + 租户隔离）。
