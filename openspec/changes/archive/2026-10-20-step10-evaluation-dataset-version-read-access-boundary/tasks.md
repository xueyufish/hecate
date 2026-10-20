# Tasks

## 1. 读取边界实现(`src/hecate/ops/api/dataset_versions.py`)

- [x] 1.1 新增 `_version_not_found()`(envelope 同形,恒定消息 `"Dataset version not found"`,零 ID)与 `_get_version_for_read(svc, ctx, dataset_id, version_id, db)`:父 Dataset 经 `_get_dataset_for_read`(复用 `hecate.ops.api.evaluation`)→ `svc.get_version` 作用域化查找 + `dataset_id` 绑定 → 失败统一 `_version_not_found()`;`_get_version_or_404` 及三个写端点零改动。验证:`git diff` 不含 `_get_version_or_404`/写端点变化。
- [x] 1.2 `GET .../versions` 改造:父 Dataset 门 → `ensure_workspace_role(VIEWER)`（异常映射为 `_forbidden`） → 按 `ctx.is_system_scope` 传 `workspace_id=None` 或 `ctx.workspace_id` 给 `svc.list_versions`。验证:可见 Dataset 正常分页;软删/跨 workspace/不存在 Dataset → 同体 404。
- [x] 1.3 `GET .../versions/{version_id}` 改走 `_get_version_for_read` + 角色检查;`GET .../diff` 源版本及 UUID 目标版本先经 helper 校验，全部归属通过后才检查角色，再补捕获 `DatasetVersionNotFoundError` → `_version_not_found()`(弃用 `str(exc)`),非法 `against` 的 422 原样。验证:detail 四种不可见条件同体 404;diff 目标不可见返回 404 而非 500。
- [x] 1.4 全部 404/403 响应体断言零 ID 回显。验证:实现审查 + 测试断言覆盖。

## 2. 边界测试(新增 `tests/test_api/test_evaluation_dataset_version_access_boundary.py`)

- [x] 2.1 镜像前两套边界测试夹具,覆盖 spec 全部场景:双 workspace 列表隔离(可见 Dataset 的版本分页/空列表 total 0)、软删 Dataset 版本不可读、detail 四种不可见(缺失/软删/跨 workspace/跨 Dataset)同体 404、diff 目标四种不可见 → 404 非 500、归属优先于角色(跨 workspace 无角色 → 404)、本 workspace 无角色 → 403、viewer/editor/admin 可读、system scope 跨 workspace、缺 workspace 非 system 拒绝不退化、非法 `against` 422、拒绝请求只读、失败响应不回显 UUID，成功响应保留既有标识字段。验证:新文件全部通过。
- [x] 2.2 diff 成功路径内容摘要与分类算法不因边界改变。验证:可见两版本的 diff 断言与既有 service 语义一致。

## 3. 既有回归(`tests/test_services/test_evaluation/test_publish_gate_versions.py` 等)

- [x] 3.1 排查 publish gate 与其他引用 dataset version service 的测试在 service 未改前提下的行为。验证:`python -m pytest tests/test_services/test_evaluation -q` 全绿。
- [x] 3.2 运行 `python -m pytest tests/test_api -q -n 4` 全量。验证:0 failed。

## 4. 回归与变更验证

- [x] 4.1 运行 `openspec validate step10-evaluation-dataset-version-read-access-boundary --strict` 与共享检查(ruff check/format、mypy 于改动文件)。验证:全部通过,`git diff` 不含 service/ORM/schema 变更。


## 5. 独立审查修正

- [x] 5.1 先复现再修复 diff 角色检查早于目标归属的问题：新增矩阵修正前 4 failed、12 passed；UUID 目标现在先经读取 helper 验证，再检查 viewer。
- [x] 5.2 补齐缺失/软删父 Dataset 在三个 GET 的优先拒绝；system/无角色身份对两个可见版本的 diff；将只读验证加强为 Dataset、Version、Item 全字段快照（成功读取、403 和 404 均验证）。
- [x] 5.3 校正规划中的检查顺序、错误类别映射及既有 API 测试描述，增加 delta spec 场景。
- [x] 5.4 运行边界测试、评测 service 与完整 API 回归、共享检查和 OpenSpec strict validation；在 verification.md 记录实际结果及范围。
