# Proposal

## Why

Dataset 版本的三个写接口(`POST /api/evaluation/datasets/{dataset_id}/versions` 冻结、`DELETE .../versions/{version_id}` 软删除、`POST .../versions/{version_id}/checkout` 恢复 live items)没有任何角色检查:viewer 甚至无角色身份都能冻结、删除版本,checkout 更会**直接替换数据集的 live 内容**。此外创建版本把 `ctx.workspace_id` 直接传给服务层,非 system 无 workspace 身份会被服务层的 `workspace_id or uuid.UUID(int=0)` 写成零 UUID 归属;删除/checkout 用的还是读边界之前的旧 `_get_version_or_404`,虽有 Dataset 绑定校验,但缺少父数据集读边界,也未区分显式 system 与无 workspace 非 system 身份的跨 workspace 权限。#293 已为读接口建立同族边界,写接口是同一能力的剩余缺口。

## What Changes

- 三个写接口统一写权限门槛:非 system 身份先经父数据集读边界(归属优先于角色,不存在/已删除/跨 workspace 一律同体 404,不回显资源 ID),删除/checkout 还需先验证版本存在、未删除、绑定该 Dataset 且归属同一 workspace,之后才要求 `ensure_workspace_role(ctx, WorkspaceRole.EDITOR)`;同 workspace 的 viewer/无角色返回 403;无 active workspace 的非 system 身份被拒绝,不退化为全局操作。
- 显式 system scope 保留跨 workspace 写入能力;写操作的归属使用**目标 Dataset 的真实 workspace**(`ds.workspace_id`),修复创建版本在 `ctx.workspace_id=None` 时被服务层写成零 UUID 归属的问题。
- 删除/checkout 从旧 `_get_version_or_404` 迁移到与读边界一致的归属解析:父数据集 → 版本存在/未删除/属于该数据集且 workspace 一致 → editor 下限;父级失败返回 Dataset 常量 404,版本级失败返回 Version 常量 404,均不回显标识。服务再次查询时的版本不存在错误也映射为相同 404。
- 保持不变:冻结内容与 content hash、重名 409、软删除语义、checkout 算法与响应、读接口及其边界、既有成功响应形状。
- 不扩展:普通 Dataset 写接口、前端、Runtime、版本服务算法。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `dataset-versioning`: 新增"Dataset 版本写访问边界"要求——三个写接口的 editor 角色下限、归属优先于角色的同体 404、system 跨 workspace 写入及真实 workspace 归属、viewer/无角色 403、无 workspace 非 system 拒绝、拒绝路径零副作用。

## Impact

- 主要修改 `src/hecate/ops/api/dataset_versions.py`(单一 API 文件);新增 `tests/test_api/test_evaluation_dataset_version_write_boundary.py`(镜像既有 `test_evaluation_dataset_version_access_boundary.py` 的夹具风格)。
- 复用 `evaluation.py` 的 `_get_dataset_for_read`/`_forbidden`/`_dataset_not_found` 与 `ensure_workspace_role`;不改 `dataset_version_service` 的算法与语义(仅调用方传入的归属改为目标数据集的真实 workspace)。
- 回归:既有读边界测试(`test_evaluation_dataset_version_access_boundary.py`)与版本服务测试保持通过。
