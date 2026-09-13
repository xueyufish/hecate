# Tasks: eval-publish-gate-versions (7.3a + 7.3b)

## 1. Schema 与迁移

- [x] 1.1 新模型 `EvaluationDatasetVersionModel`：`dataset_id`、`name`、`description`、`items`（冻结 JSON）、`content_hash`、`created_by`、`workspace_id` + BaseModel 审计列；`(dataset_id, name)` 唯一（含软删除行，名字不释放）
- [x] 1.2 `EvaluationRunModel` 加 `dataset_version_id`（nullable + index）；`WorkflowModel` 加 `evaluation_gate`（JSON，nullable = off）；Workflow Update/Read schemas 带门禁配置校验（require 模式全信号禁用拒绝、`min_pass_rate` 越界 [0,1] 拒绝）
- [x] 1.3 单个 Alembic 迁移：建表 + 两处加列（加性向后兼容，downgrade 删除）

## 2. 版本对象服务（7.3b 后端核心）

- [x] 2.1 把 `_snapshot_dataset` 的 canonical 序列化与内容六字段投影 hash 提为共享函数——runner 快照与版本冻结共用同一实现点（design D4）
- [x] 2.2 `dataset_version_service.py`：create（冻结当前 live items + hash + `created_by` 服务端填充）/ list / get / soft delete；name 冲突 409；软删后名字保留；版本不可变（无 update 路径）
- [x] 2.3 checkout：live items 全部软删 + 版本 items 重插（新 item id，内容/tags/metadata/known_bad 及 provenance 原样保留）；响应携带 diff 摘要（added/removed/changed 计数）
- [x] 2.4 diff：按 item id 对齐 + content-only hash 分类 `added/removed/changed`（changed 带字段级 delta）；`against=version_id|live` 双模式

## 3. 版本 API

- [x] 3.1 新路由：`POST/GET/DELETE /api/evaluation/datasets/{id}/versions(/{vid})` + `POST .../checkout` + `GET .../diff`；workspace 归属校验 + 404/409/422 错误路径
- [x] 3.2 请求/响应 schemas 按命名规范（`XxxCreateSchema` / `XxxReadSchema`）

## 4. Run 版本绑定

- [x] 4.1 评估触发请求体加可选 `dataset_version_id`（存在性 + workspace 校验，未知/越权拒绝且不建 run）
- [x] 4.2 runner：绑定 run 的 `dataset_snapshot` 直接取版本 items + hash（snapshot 记录 version id/name）；成本护栏按版本 items 计数（非 live 计数）
- [x] 4.3 engine 新增可选 items override：绑定 run 显式执行版本 items；无绑定路径行为逐字节不变（live 冻结 + 完成时 drift 对比）
- [x] 4.4 summary 持久化生效 `threshold`（配置存在时；无配置不出键）
- [x] 4.5 CLI `hecate workflow eval run` 加 `--dataset-version-id`；输出 JSON 标识绑定版本；三态退出码语义不变

## 5. 门禁与 publish 契约（7.3a 后端核心）

- [x] 5.1 `ops/evaluation/publish_gate.py`：GateResult 判定单点——确定性分数重算 per-item `deterministic_pass_rate`（threshold 取 summary）、确定性 metric 均值 vs baseline run 回归、`block_on_drift`（snapshot hash vs live hash）、`require_run`、`require_dataset_version`、`no_deterministic_scores`；LLM judge / human 分数永不参与
- [x] 5.2 `_build_evaluation_report` 增强：`gate` 块（mode + 逐信号 verdict）+ `dataset_version` 块（run 绑定时的 id/name/hash）；run 的 `captured_at` 与 snapshot hash 暴露（陈旧可见）
- [x] 5.3 `publish_version` 接门禁：require 且启用信号不满足 → 409 `EVALUATION_GATE_BLOCKED`（错误信封 details 携带 gate + 完整 report）；warn → 200 report 附加判定；off（列 NULL）→ 响应与现状逐字节一致
- [x] 5.4 publish 端点可选 JSON body `{"force": true}`：绕过 + `gate.bypassed_by_force` + 审计记录（动作 + 操作者）
- [x] 5.5 workflow PATCH 门禁配置校验接线（复用 1.2 校验，拒绝时存量配置不变）

## 6. Web UI（偿还 7.3c 欠账 + 新能力界面）

- [x] 6.1 dataset 详情：版本面板（list / create / delete + checkout 二次确认）
- [x] 6.2 diff 视图：version vs version、version vs live（复用 dataset_drift 渲染风格）
- [x] 6.3 item 列表 known-bad 标记入口（徽标 + 标记/解除动作 + reason 必填校验）
- [x] 6.4 workflow publish 流程：gate 配置入口、阻断态完整呈现（report + gate）、force 二次确认
- [ ] 6.5 评估触发表单支持选择 dataset 版本（含"不绑定"默认项）

## 7. 测试

- [x] 7.1 dataset_version_service：冻结与 hash 一致性（同 item 集与 run 快照 hash 相等）、name 冲突 409、软删名字保留、checkout 恢复 + known_bad provenance 保留、diff 三类分类与字段级 delta、vs live 模式
- [x] 7.2 runner/engine：绑定 run 快照取版本且记录版本元信息、执行版本 items（live 编辑不影响进行中 run）、护栏按版本计数（版本少/live 多时通过）、无绑定路径回归不变、summary threshold 有配置才出键
- [x] 7.3 publish_gate：确定性重算口径（如 10 活跃 8 过 → 0.8）、LLM-only 回归不阻断、`no_deterministic_scores`、drift / require_run / require_dataset_version 各信号独立判定、warn 不阻断、force 绕过 + bypassed 标记、off 时零行为差异
- [x] 7.4 API：版本端点全路径（含 404/409/422）、trigger 绑定（未知/越权拒绝且不建 run）、publish 409 信封形状、workflow PATCH 门禁校验拒绝路径
- [ ] 7.5 CLI：`--dataset-version-id` 绑定生效、三态退出码语义不变

## 8. 验证与交付

- [x] 8.1 四项全绿：`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/ -q`
- [x] 8.2 Conventional commit 已落（`23d64a0`）；push 前需用户在对话中明确确认（尚未 push）
