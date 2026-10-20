# Tasks

## 1. 写边界实现

- [x] 1.1 `dataset_service.py`:`add_items` 保持原签名,从已查询父 Dataset 取得 workspace 并写入新行,覆盖 API 与 service 直调方,不信任 item payload 的 workspace;`update_item`/`delete_item` 签名不变。验证:service 单测/类型检查通过,既有直调方不受影响。
- [x] 1.2 `evaluation.py`:复用 `_get_item_or_404` 的恒定无 ID 404,新增 `_get_item_for_write(ctx, dataset_id, item_id, db)`(父 Dataset 经 `_get_dataset_for_read` → item 绑定校验 → 返回 item);三个写端点统一「父 Dataset 门 →(item 绑定)→ `ensure_workspace_role(EDITOR)`」顺序,`add_items` 由 service 从父 Dataset 继承 workspace。验证:三个端点对不可见 Dataset / 跨 Dataset / 缺绑定样本返回同体 404,可见 Dataset 行为不变。
- [x] 1.3 `_get_dataset_or_404`/`_get_item_or_404` 既有调用语义与全部读取 helper 零变化。验证:`git diff` 不含其定义变化;并行版本写 change 的文件(`dataset_versions.py`)不在本 diff。

## 2. 写边界测试(新增 `tests/test_api/test_evaluation_dataset_item_write_boundary.py`)

- [x] 2.1 镜像既有边界测试夹具,覆盖 spec 全部场景:双 workspace 隔离(可见 Dataset 正常写)、跨 Dataset item_id 的 PATCH/DELETE 零效果且受害者记录全字段不变(内容、删除标记、known-bad provenance)、不可见 Dataset 三写同体 404、viewer/无角色 → 403 且 provenance 不变、editor/admin 正常(含 known-bad 标记/清除与 provenance 服务端填充)、system scope 跨 workspace、缺 workspace 非 system 拒绝、新样本 workspace 继承父 Dataset、历史零 UUID 样本仍可读写、空 query 跳过计数、非法标记 422 保留、拒绝只读。验证:新文件全部通过。
- [x] 2.2 404/403 响应体零资源 ID 回显断言。验证:测试显式覆盖。

## 3. 既有回归(`tests/test_api/test_evaluation_api.py` 等)

- [x] 3.1 排查并按预期修正隐含旧行为的既有用例(如断言新样本零 UUID workspace);ADMIN 身份的既有成功用例应保持绿。验证:`python -m pytest tests/test_api/test_evaluation_api.py tests/test_api/test_evaluation_dataset_item_write_boundary.py tests/test_api/test_evaluation_dataset_access_boundary.py -q` 全绿。

## 4. 回归与变更验证

- [x] 4.1 运行 `python -m pytest tests/test_api -q -n 4` 全量。验证:0 failed。
- [x] 4.2 运行 `openspec validate step10-evaluation-dataset-item-write-access-boundary --strict` 与共享检查(ruff check/format、mypy 于改动文件)。验证:全部通过,`git diff` 不含 dataset_versions.py、版本规范、前端或 Runtime 变更。


## 5. 复审修正与验证

- [x] 5.1 补充 service 直调继承父 workspace 的回归,在原实现上确认失败后修正;保持原签名,历史行不回填,忽略 payload 中伪造的 workspace。
- [x] 5.2 补齐低角色下父归属和 item 绑定优先于角色、缺失/软删除/跨 Dataset/跨 workspace 同体 404、system 三写与新行归属、provenance 精确身份/清除回归;拒绝副作用快照直接读取全部持久列。
- [x] 5.3 修正文档中过度宣称“评测域最后写缺口”、新增 helper 和 service 参数与实际实现不符之处。
- [x] 5.4 复跑相关 API 与 evaluation service 回归、共享 `check all`、OpenSpec strict、feature inventory 与差异范围检查,将实际结果写入 verification.md。
