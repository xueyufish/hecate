# Tasks: known-bad-exemption (7.3c)

## 1. Schema 与迁移

- [x] 1.1 `EvaluationItemModel` 新增五列：`known_bad`（bool，default false）、`known_bad_reason`、`known_bad_marked_by`、`known_bad_marked_at`、`known_bad_expires_at`（均 nullable）；在当前 Alembic head 上落迁移（加列向后兼容，downgrade 删列）
- [x] 1.2 扩展 item schemas（Create/Update/Read）：Update 接受 `known_bad` / `known_bad_reason` / `known_bad_expires_at`；`known_bad_marked_by` / `known_bad_marked_at` 为服务端只读字段；校验 `known_bad=true` 时 reason 非空

## 2. Dataset 服务与 API

- [x] 2.1 `EvaluationDatasetService` 更新路径：标记时服务端填充 `known_bad_marked_by`（当前认证用户）与 `known_bad_marked_at`；解除时四个 marker 字段全部重置为 `None`
- [x] 2.2 `list_items` 增加豁免状态过滤（only known-bad / only active / 不过滤）
- [x] 2.3 `import_json` / `export_json` round-trip 豁免字段；import 对缺失字段按"未标记"处理（兼容旧导出文件）
- [x] 2.4 items API 端点接线与错误路径（缺 reason 返回 422 校验错误，item 不变）

## 3. Run 快照

- [x] 3.1 `_snapshot_dataset`：快照 item dict 仅在已标记时附加豁免字段（稀疏序列化）；content hash 只投影 `query/expected_answer/context/tags/metadata` 六字段计算——标记/解除不影响 hash、不触发 `dataset_drift`

## 4. 引擎聚合

- [x] 4.1 `_build_summary`：按快照豁免状态把 known-bad item 排除出 `pass_rate` / `consistency_rate` 的分子分母；summary 暴露 `exempted_items` 计数与 `known_bad_passed_item_ids`（全部重复都达标的豁免 item，只报警不清除）
- [x] 4.2 `_compute_regressions`：metric 平均与 `is_regression` 判定只统计 active item（compare/diff 经 summary 自动继承口径，不单独改动）

## 5. 测试

- [x] 5.1 dataset service：标记（含 provenance 填充）/缺 reason 拒绝/解除重置/过滤/import-export round-trip + 旧文件兼容
- [x] 5.2 offline runner：稀疏序列化（未标记 item 无新键）、hash 对标记不敏感、标记后 run 不报 `dataset_drift`
- [x] 5.3 engine：10 条 2 豁免 6 过 → `pass_rate=0.75` + `exempted_items=2`；豁免项全通过进 `known_bad_passed_item_ids` 且不进 `pass_rate`；run 完成后标记不改变该 run summary；`repetitions=1` 时不出现 `consistency_rate`
- [x] 5.4 API：items PATCH 标记/解除/422 路径；`POST /runs/compare` 两端各自按快照口径排除豁免项
- [x] 5.5 CLI 三态退出码在含豁免 item 的 dataset 上行为不变（exit 0/2/3 语义基于排除后 pass_rate）

## 6. 验证与交付

- [x] 6.1 四项全绿：`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/ -q`
- [x] 6.2 Conventional commit（`feat(evaluation): known-bad exemption labeling (7.3c)`）；push 前需用户在对话中明确确认
