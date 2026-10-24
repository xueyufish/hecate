# Design — step10-dataset-synthesis-access-boundary

## Context

合成域由三个入口组成：`POST /api/evaluation/datasets/synthesize`（`src/hecate/ops/api/synthesis.py`，`count <= 20` 同步直跑 pipeline、`> 20` 落 `DatasetSynthesisJobModel` 行后 `asyncio.create_task` 后台执行）、pipeline façade `DatasetSynthesisService.synthesize()`（`src/hecate/ops/evaluation/synthesis/service.py`）、job 生命周期 `DatasetSynthesisJobService`（`src/hecate/ops/evaluation/synthesis/job.py`，`get_job` 供轮询端点使用）。

当前三个入口均无授权边界：API 层用请求体 `workspace_id` 直接覆盖归属（且代码重复两遍）；`_load_seeds` 只按 `dataset_id` 查 item；`get_job` 在 `workspace_id=None` 时取消租户过滤。step10 边界系列已确立的惯例（ownership 先于 role、恒定无 ID 404、`NO_WORKSPACE` 403、system 跨租户豁免、`ensure_workspace_role` 闸门）见 `evaluation.py` 的 `_dataset_not_found`/`_no_workspace`/`_forbidden`/`_get_dataset_for_read` 与 `core/deps_workspace.py`。

约束：不改公开 schema（`DatasetSynthesisJobCreateSchema` 的 `workspace_id` 字段保留为 system 选择器）、不改共享 helper 签名、不改迁移/依赖/composition、不碰在途的 Task 管理写边界包（`evaluation_tasks.py`）。

## Goals / Non-Goals

**Goals:**

- 两个端点 + pipeline 执行期形成完整边界：身份 → 目标 workspace 解析 → 种子归属校验 → 角色闸门 → 副作用，顺序固定。
- 合成产物（目标 Dataset、Item、Job 行）一律携带验证后的目标 workspace，零 UUID 归属经 API 不可达。
- 种子失效（排队后被删）显式失败，杜绝静默回退 topic 或空种子生成。

**Non-Goals:**

- 不改同步/异步分界、响应结构、策略/过滤/tags 语义（spec 的 preservation scenario 覆盖）。
- 不做 DLP/出站治理、后台任务恢复机制重构。
- 不动在途 Task 管理包的任何文件。

## Decisions

1. **目标 workspace 解析集中在 API 层一个 helper（`_resolve_target_workspace(ctx, requested) → uuid.UUID`）**
   - 非 system：`ctx.workspace_id is None` → `_no_workspace()`；`requested` 非空且 ≠ 自身 → 恒定 FORBIDDEN（不查询目标是否存在，响应恒定故不泄露存在性）；否则取 `ctx.workspace_id`。
   - system：`requested` 为空 → `_no_workspace()`；否则按 id 查 `WorkspaceModel`（存在 + 未删除），不可解析 → `_no_workspace()`。
   - 替代方案：foreign `workspace_id` 返回 404——放弃，因为发起方已持有自身 workspace 上下文，显式选择器写别的 workspace 是授权错误（与 #300 创建边界「写他人 workspace」语义一致），且恒定 403 不泄露目标存在性。
2. **种子校验为 service 层方法，API 与 pipeline 共用同一实现**
   - 在 `DatasetSynthesisService` 增加 `validate_seed(seed_dataset_id, workspace_id) → EvaluationDatasetModel`：按 id + 未删除查询，`ds.workspace_id != workspace_id` 或查无 → 抛 `SeedDatasetUnavailableError`（service 层新异常，不携带标识符信息）。API 层 catch → `_dataset_not_found()`（复用 evaluation.py 的恒定 envelope）；`_execute_job` catch → `_mark_failed("seed dataset unavailable")`。
   - 替代方案：API 层自写局部 helper、pipeline 另写一遍——放弃，两处语义必然漂移（TOCTOU 修复的教训，见 #293/#299）。
   - `_load_seeds` 改为：先 `validate_seed`，再按 `dataset_id` + 未删除读 item，**不加 item.workspace_id 条件**（样本归属锚定父 Dataset，零 UUID 历史样本仍是合法种子，与 #296 一致）。
3. **`synthesize()` 内校验顺序前移**：_load_seeds（有种子时，先 validate_seed，再读取种子）→ 建 target Dataset → 策略生成 → 过滤 → 落库。同步路径 API 层已前置校验，pipeline 内复查是竞态防御；种子失效异常向上传播：同步路径由 API 层 catch 映射 404（先于任何创建，因 API 层已校验，此处仅竞态窗口内可达），异步路径由 `_execute_job` 的既有 `except` 捕获 → `_mark_failed`。
   - 注意：`synthesize()` 抛 `SeedDatasetUnavailableError` 不能被既有 `except ValueError → 400` 吞掉，新异常必须不继承 `ValueError`（直接继承 `Exception`）。
4. **job 轮询在 API 层守卫，`get_job` 签名不变**
   - 非 system：`ctx.workspace_id is None` → 恒定 404（与 #299/#303 的按 ID 读取惯例一致：无法定位可见性即视为不可见，先于查询）；否则 `get_job(job_id, workspace_id=ctx.workspace_id)`。
   - system：`get_job(job_id, workspace_id=None)`（现状即跨租户按 ID 读，job 行自带真实 workspace，无需二次约束）。
   - `get_job` 返回 None → 既有 `Synthesis job not found` 404 envelope（已无 ID，保持）。可见性判定后再 `ensure_workspace_role(ctx, VIEWER)` → `_forbidden`。
5. **`ensure_workspace_role` 阈值**：POST = `WorkspaceRole.EDITOR`（写语义，对齐 #300 创建边界）；GET = `WorkspaceRole.VIEWER`（读语义）。system 天然豁免（helper 既有行为）。
6. **顺带清理 `synthesis.py`**：删除 56-65 行重复死代码块；底部 `import asyncio  # noqa: E402` 移至顶部标准 import 区。

## Risks / Trade-offs

- [后台种子失效只在 pipeline 启动时校验，运行中（模型调用中途）种子被删仍可能已完成部分生成] → 接受：与「排队后失效」的威胁模型一致，运行中快照语义属于后台任务恢复机制重构（非目标）。
- [system 指定不存在/已删除的 workspace 得到 `NO_WORKSPACE` 而非 404] → 与 #300「创建必须有可解析 active workspace」语义一致，spec 已固定；若未来需要区分，属新增错误码的独立变更。
- [pipeline 二次校验使异步 Job 的失败路径新增一种显式错误文案] → `error_message` 为自由文本（截断 2000），无消费方解析具体文案，兼容。
- [既有 service 层测试使用零 UUID workspace 创建 job] → `create_job`/`get_job` 签名不变，零 UUID 仅是普通值，既有测试不受影响。

## Migration Plan

纯 API 授权收紧，无数据迁移、无 schema 变更。回滚 = revert 提交。部署顺序无要求（单服务单体）。

## Open Questions

（无）
