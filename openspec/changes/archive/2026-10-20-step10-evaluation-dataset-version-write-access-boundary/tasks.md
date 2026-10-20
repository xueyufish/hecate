# Tasks

## 1. 写边界实现

- [x] 1.1 创建使用 `_authorize_dataset_write`:父数据集归属 → editor;删除/checkout 使用 `_get_version_for_write`:父数据集归属 → 版本存在/未删除/workspace 一致/Dataset 绑定 → editor。服务调用使用 `ds.workspace_id`,移除旧 `_get_version_or_404`。
- [x] 1.2 创建/checkout 的父级失效返回 Dataset 常量 404;删除/checkout 的版本再次查询失效返回 Version 常量 404。保留冻结/hash/409/软删除/checkout 算法与响应。

## 2. 写边界测试

- [x] 2.1 新增三写端点身份与归属矩阵,覆盖可见资源 viewer/无角色 403、不可见父级和版本先于角色 404、editor/admin 正常写入、system 跨 workspace 真实归属、无 workspace 拒绝;拒绝路径比较数据库全表状态。
- [x] 2.2 复审先补失败回归:viewer/无角色不可见版本错误返回 403,system 接受归属不一致版本导致未处理异常。修正边界顺序与版本 workspace 过滤;补服务再次查询失效、冻结不变性、checkout 恢复内容与软删除重名回归。
- [x] 2.3 回归既有 Dataset/Version 读边界、评测 API 与版本服务测试,确认原有语义不回归。

## 3. 门禁与收尾

- [x] 3.1 运行共享检查 `python scripts/dev.py check all` 与受影响测试,确认零失败,记录实际命令及结果。
- [x] 3.2 运行 OpenSpec 严格校验与功能清单检查,更新 `verification.md` 的实际结果与未测范围。
