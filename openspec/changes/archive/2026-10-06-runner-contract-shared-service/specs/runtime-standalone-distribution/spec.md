# Spec Delta

## ADDED Requirements

### Requirement: 共享执行应用服务

`hecate-runtime` MUST 提供平台无关执行应用服务,统一驱动已装配 runtime、捕获事件、执行 observer 决策,并将结果归类为 succeeded、failed、cancelled 或 unknown。服务 MUST NOT 拥有平台 Task/Run 状态、数据库、身份、授权、证据或 HTTP 协议;调用方 MUST 通过 adapter 绑定这些责任。内置平台后端与独立宿主 SHALL 消费同一服务并保持各自存储装配。

#### Scenario: 内置后端与独立宿主共享执行行为

- **WHEN** 平台内置后端和独立宿主分别通过共享服务执行
- **THEN** 事件顺序、失败归类、合作取消和 unknown 停止语义来自同一实现,适配层只追加各自持久化与治理事实

#### Scenario: 服务保持运行时独立

- **WHEN** 构建 `hecate-runtime` wheel
- **THEN** 执行应用服务不依赖完整 Hecate 应用、SQLAlchemy ORM、FastAPI 或平台管理表
