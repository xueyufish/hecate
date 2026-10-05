# Proposal

## Why

演进方案 step5c 仍有关键未完成项:独立宿主已能本地执行,但仍使用专用固定图执行循环,HTTP 请求/回执/事件/能力声明尚未对齐 step3 的 execution-backend 绑定。与此同时,内置后端和独立宿主分别驱动 Pregel、捕获事件和归类失败,后续跨语言/可替换后端会重复这些语义并产生漂移。

本 change 是迭代 1 的落地切片:先固定共享执行应用行为和公开协议适配,不提前引入受管投递、动作时授权、持久等待或工作流回调。

## What Changes

- **共享执行应用服务**:`hecate-runtime` 新增平台无关执行应用服务,统一 Pregel 驱动、事件捕获、observer 决策、失败/取消/未知结果归类;平台内置后端与独立宿主消费同一服务,各自保留身份、存储、证据和持久化 adapter。
- **Runner 正式契约绑定**:`hecate-runner` 的 HTTP 面按权威 JSON Schema/OpenAPI 语义接受 `ExecutionRequest`,返回 `SubmitReceipt`、`RunStatus`、`EventPage`、`CancelReceipt` 和 artifact 引用;请求校验只依赖随独立 wheel 发布的 schema,不导入完整 Hecate 应用。
- **持久事件与重启查询**:durable profile 将本地执行事件写入既有 durable event log,使用稳定 event id 和 source sequence;进程重启后可按 backend run ref 查询状态与事件游标。
- **兼容边界**:本地 preview API 保留为兼容视图;正式客户端通过契约请求/响应使用同一路径。受管通道、审批、动作时授权和恢复对账不在本 change 扩张。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `runtime-standalone-distribution`:运行时独立包新增共享执行应用服务,内置后端与独立宿主双消费,不引入平台存储或 ORM 依赖。
- `standalone-runner-host`:宿主 HTTP 面新增 step3 正式契约绑定、持久事件映射和重启后状态/事件查询。

## Impact

- `packages/hecate-runtime`:新增 execution service 及单元测试;内置后端改为消费该服务。
- `packages/hecate-runner`:新增 contract adapter,server 使用正式响应,engine 消费共享服务并写入 durable event log。
- `packages/hecate-durable`:event log 支持稳定 event id 的幂等 emission。
- `packages/hecate-runner`:包内携带与权威执行 schema 同步校验的发布快照,保证 sdist/wheel 均可独立构建。
- 测试:Runner 本地契约/幂等/重启查询、runtime 服务行为、内置后端回归、schema 随 wheel 发布检查。
