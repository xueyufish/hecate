# Proposal

## Why

评测样本(item)的三个写接口存在跨 workspace 写洞:`POST /datasets/{id}/items` 与 `DELETE /datasets/{id}/items/{item_id}` 只查路径 Dataset 存在(`_get_dataset_or_404`,无归属校验)且无任何角色检查;`DELETE` 更是完全不校验 `item_id` 与路径 Dataset 的绑定——任何已认证身份可经自己的 Dataset 路径删除**任意 workspace 任意 Dataset** 的样本。`PATCH`(known-bad 标记)虽有 item→Dataset 绑定,但从不校验该 Dataset 的 workspace 归属,W1 的 editor 可改 W2 样本的豁免标记。此外新增样本不继承父 Dataset 的 workspace,落到模型默认零 UUID。#287/#290/#293 已收紧 Run/Dataset/版本的读取边界;本 change 继续收紧样本写入口,其余评测写入口仍需独立治理。

## What Changes

- **三个样本写接口落地写边界**(与读边界同构、角色地板为 `editor`):先校验父 Dataset 归属与删除状态,再校验样本与路径 Dataset 的真实绑定(PATCH/DELETE),全部归属通过后检查最低 `editor` 角色;归属优先于角色,不可见或绑定不匹配统一返回不回显资源 ID 的一致 404。
- **授权锚定父 Dataset**:workspace 判定依据父 Dataset 归属与 item→Dataset 真实绑定,SHALL NOT 以样本行上的历史 `workspace_id` 字段为准(部分历史样本为零 UUID,不回填);新增样本继承父 Dataset 的真实 workspace。
- **身份规则**:显式 system scope 跨 workspace;缺 active workspace 的非 system 身份在进入 service 前拒绝。
- **既有语义不变**:known-bad 标记/清除与服务端 provenance 填充、空 query 跳过、返回形状(`{"added": n}`、201、204)保持;读接口与既有读取 helper(`_get_dataset_for_read` 等)签名语义不变;Dataset CRUD、Run 触发、版本接口、Runtime、前端不在范围内。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `evaluation-dataset`: 新增「Evaluation dataset item write access boundary」需求,约束三个样本写接口的父 Dataset 归属、样本绑定、editor 角色下限、一致无 ID 404、workspace 继承与身份规则;既有 known-bad 标记、成功形状场景在边界内保持为真,无需修改。

## Impact

- `src/hecate/ops/api/evaluation.py`:新增样本写校验 helper(父 Dataset 门 + item 绑定,统一无 ID 404),角色检查留在端点且位于归属检查之后;三个写端点改造;`_get_dataset_or_404`/`_get_item_or_404` 的既有调用语义不受并行版本写 change 影响。
- `src/hecate/ops/evaluation/dataset_service.py`:`add_items` 保持原签名,使用已查询的父 Dataset workspace 写入新样本,覆盖 API 与现有合成服务直调方;`update_item`/`delete_item` 签名不变。
- 测试:新增 `tests/test_api/test_evaluation_dataset_item_write_boundary.py`(镜像既有边界测试夹具);`tests/test_api/test_evaluation_api.py` 既有用例以 ADMIN 身份运行应保持绿,断言零 UUID workspace 的用例属预期修正。
- 不改 `update_item`/`delete_item` service 签名、读 helper、版本接口(dataset_versions.py 归并行 change)、Run 触发、前端。
