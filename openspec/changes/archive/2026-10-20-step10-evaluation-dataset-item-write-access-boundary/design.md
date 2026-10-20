# Design

## Context

#287(Run 读)、#290(Dataset 读)、#293(版本读)之后,本 change 聚焦样本写接口缺口,其他评测写入口仍需后续切片:`add_items`/`delete_item` 只经 `_get_dataset_or_404`(存在 + `~deleted`,无归属),`update_item` 经 `_get_item_or_404`(item→dataset 绑定,无 Dataset 归属);三者均无角色检查。`delete_item` 的 service 只按 `id + ~deleted` 查找,构成跨 Dataset/跨 workspace 删除洞。`add_items` 构造样本不含 `workspace_id`,落模型默认零 UUID(模型 `default=lambda: uuid.UUID(int=0)`);部分历史样本为零 UUID。`_get_item_or_404` 仅 PATCH 一个调用点;`_get_dataset_or_404` 仍被 dataset PUT/DELETE 与 add_items/delete_item 共用。

## Goals / Non-Goals

**Goals:**

- 三个样本写接口获得与读边界同构的父 Dataset → 样本绑定 → 角色三级校验,角色地板 `editor`;404 类内同体、零 ID 回显。
- 新样本继承父 Dataset workspace;历史零 UUID 样本不回填、授权不受影响。

**Non-Goals:**

- 不改 Dataset CRUD、Run 触发、版本接口(并行 `step10-evaluation-dataset-version-write-access-boundary` 负责 dataset_versions.py)、前端、Runtime。
- 不改既有读取 helper 签名语义;不回填历史 `workspace_id`;不引入新的权限抽象。

## Decisions

### D1:写边界 helper 复用 #290 的 dataset 读取门,角色地板升为 editor

新增 `_get_item_for_write(ctx, dataset_id, item_id, db)`:先 `_get_dataset_for_read(ctx, dataset_id, db)`(父 Dataset 存在 + `~deleted` + workspace/scope → 否则既有 "Dataset not found" 404;该 helper 名字含 read 但语义就是"按作用域解析可见 Dataset",写路径复用它做归属门,避免第二份实现),再 item 绑定校验(id + dataset_id + `~deleted` → 否则复用 `_get_item_or_404` 的恒定 "Item not found"),返回 item。角色检查留在端点(`ensure_workspace_role(ctx, WorkspaceRole.EDITOR)` → `_forbidden`)。三个端点顺序统一:父 Dataset 门 → item 绑定(add 无 item)→ 角色。

- 备选:参数化 `_get_dataset_for_read` 的角色地板——被否决:该 helper 被 4 个读端点共用,加参数扩大影响面;写端点在门之后自查角色即可。
- 备选:直接改 `_get_dataset_or_404` 加 ctx——被否决:dataset PUT/DELETE 写路径共用,行为变化属 Dataset CRUD 写边界后续切片。

### D2:授权锚定父 Dataset,历史零 UUID 不回填

item 的可写性完全由「父 Dataset 可见 + item 绑定该 Dataset」决定,不读 `item.workspace_id`。`add_items` 保持原签名,复用其现有 `get_dataset` 查询返回值,始终将父 Dataset 的真实 `workspace_id` 写入新行。API 无需传入 workspace,现有合成服务直调也继承同一归属;不允许 item payload 指定归属,system scope 同样按父 Dataset 归属写入。历史行不回填。原实现只在 API 显式传参时归位,直调方仍写零 UUID;复审已通过失败回归复现并修正。

### D3:DELETE 补绑定校验,`svc.delete_item` 签名不变

DELETE 端点经 `_get_item_for_write` 获得绑定校验后,`svc.delete_item(item_id)` 保持原签名——service 只处理已绑定 item 的软删。`update_item` 签名同样不变(PATCH 已有绑定语义,迁到新 helper)。

### D4:404 两类恒定、零 ID 回显

父 Dataset 不可见 → 既有 "Dataset not found";样本不可见/绑定不匹配 → 复用既有 `_get_item_or_404` 的 "Item not found"。PATCH 原有的 422 `INVALID_EXEMPTION`(标记无理由)路径原样保留。

### D5:测试镜像既有边界套件

新增 `tests/test_api/test_evaluation_dataset_item_write_boundary.py`,复用夹具模式;重点:跨 Dataset 拼接 item_id 的 PATCH/DELETE 零效果(含对受害者记录的全字段不变断言)、双 workspace 隔离、viewer/无角色 → 403、editor/admin/system 正常、缺 workspace 拒绝、新样本 workspace 继承、known-bad provenance 拒绝前后不变、空 query 跳过计数、拒绝只读。复审补充低角色下父 Dataset/item 绑定优先顺序、缺失/软删除/跨 Dataset/跨 workspace 样本同体 404、system DELETE 与新行归属、service 直调归属回归。快照查询直接读取表的全部持久列,避免 ORM identity map 缓存掩盖 API session 的更新。回归:`test_evaluation_api.py`(ADMIN 身份应保持绿)+ `tests/test_api` 全量。

## Risks / Trade-offs

- [新样本 workspace 从零 UUID 变为父 Dataset workspace 是可观察变更] → 即本 change 目的;既有用例若断言零 UUID 属预期修正。
- [DELETE 此前可越权删除任何样本] → 修复后跨 workspace 删除返回 404;无兼容价值,spec 显式钉死。
- [`_get_dataset_for_read` 名字含 read 被写路径复用] → 语义是"按作用域解析可见 Dataset";若实现时觉得歧义,可局部薄封装但保持单一实现,不复制查询。

## Migration Plan

纯 API 层收紧 + 新样本字段归位,无数据迁移、无契约版本变更。回退 = 代码回滚,无持久状态需要恢复(新样本的 workspace 字段为正确值,回退后不影响读)。

## Open Questions

(无)
