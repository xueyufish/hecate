# Tasks

## 1. 共享执行应用服务

- [x] 1.1 在 `hecate-runtime` 实现 runtime 驱动服务:事件捕获、observer 决策、失败/合作取消/调度取消归类,不引入平台依赖。验证:runtime 单元测试覆盖成功、失败、取消、unknown 停止。
- [x] 1.2 平台内置后端改用共享服务并保持既有契约行为。验证:`tests/test_execution/test_builtin_backend.py` 与共享契约测试通过。
- [x] 1.3 Runner 引擎改用共享服务,保留本地证据、durable ledger 和串行准入 adapter。验证:Runner engine/durable 既有测试通过。

## 2. Runner 正式契约绑定

- [x] 2.1 Runner wheel 包含权威执行 schema,并提供本地 registry 校验。验证:干净 wheel 安装检查 schema 资源存在且请求校验不访问网络。
- [x] 2.2 HTTP 面支持 `ExecutionRequest`、`SubmitReceipt`、`RunStatus`、`EventPage`、`CancelReceipt` 与 artifact 引用。验证:响应通过权威 schema;preview 回归不破坏。
- [x] 2.3 正式提交绑定 header/body idempotency key 与 server-verified subject,重放返回原 run。验证:同 key 同请求不重复执行,异请求返回 409。

## 3. 持久事件与重启查询

- [x] 3.1 durable event emission 支持稳定 event id 幂等。验证:重复 emit 同 id 同内容不新增 sequence,异内容冲突。
- [x] 3.2 Runner 执行事件写入 durable event log,状态查询可在重启后从 durable 记录恢复授权视图。验证:重启后按结构化 run ref 查询终态和事件游标。

## 4. 验证与文档

- [x] 4.1 更新 Runner README 与演进方案状态,明确正式绑定已交付、受管/授权/等待/恢复仍属后续迭代。验证:文档与代码一致。
- [x] 4.2 运行 scoped pytest、ruff check/format、mypy;必要时补 wheel 干净安装验证。验证:本地门禁零错误。
