## ADDED Requirements — 新增需求

### 需求：SecurityEvent 统一数据模型
系统应定义一个 `SecurityEvent` 数据类，将来自 AuditLog、ToolDecision 和 SecurityFinding 的事件归一化为单个模式。每个 SecurityEvent 应包含：event_type（`api` | `tool_policy` | `anomaly`）、severity（`info` | `low` | `medium` | `high` | `critical`）、source（`audit_log` | `tool_decision` | `security_finding`）、timestamp、actor（user_id 和/或 agent_id）、action、decision、resource 和 metadata（可扩展）。

#### 场景：AuditLog 归一化为 SecurityEvent
- **当** 收集到 `action="agent.create"` 且 `success=true` 的 AuditLog 记录时
- **则** 创建 event_type="api"、severity="info"、source="audit_log" 的 SecurityEvent

#### 场景：ToolDecision 归一化为 SecurityEvent
- **当** 收集到 decision="DENY" 的 ToolDecision 时
- **则** 创建 event_type="tool_policy"、severity="high"、source="tool_decision" 的 SecurityEvent

#### 场景：SecurityFinding 归一化为 SecurityEvent
- **当** 收集到 severity="critical" 的 SecurityFinding 时
- **则** 创建 event_type="anomaly"、severity="critical"、source="security_finding" 的 SecurityEvent

### 需求：SIEMExporter 可插拔接口
系统应定义一个 `SIEMExporter` ABC，带有一个 `export(events: list[SecurityEvent])` 异步方法。可以同时注册多个导出器。系统应提供一个 `NullSIEMExporter` 作为默认的无操作实现。

#### 场景：多个导出器接收相同事件
- **当** 刷新一批 10 个 SecurityEvent 时
- **则** 每个注册的导出器接收相同的一批 10 个事件

#### 场景：导出器故障不影响其他导出器
- **当** webhook 导出器发送事件失败时
- **则** syslog 导出器仍然接收并处理该批事件
- **且** 错误被记录

### 需求：WebhookSIEMExporter
系统应提供一个 `WebhookSIEMExporter`，将 SecurityEvent 批作为 JSON HTTP POST 请求发送到可配置的端点。它应支持通过 bearer token 或自定义标头进行身份验证。它应使用可配置的批大小和刷新间隔。

#### 场景：Splunk HEC 格式
- **当** `SIEM_WEBHOOK_FORMAT=splunk_hec` 且事件被刷新时
- **则** 导出器发送 POST，每个事件包装为 `{"event": {...}, "time": timestamp}` 到配置的 URL，带 `Authorization: Splunk <token>` 标头

#### 场景：通用 JSON 格式
- **当** `SIEM_WEBHOOK_FORMAT=json` 且事件被刷新时
- **则** 导出器发送带 `{"events": [...]}` JSON 体的 POST 到配置的 URL

#### 场景：通过 bearer token 进行身份验证
- **当** 设置了 `SIEM_WEBHOOK_TOKEN` 时
- **则** 导出器向所有请求添加 `Authorization: Bearer <token>` 标头

#### 场景：临时故障重试
- **当** webhook 端点返回 HTTP 503 时
- **则** 导出器最多重试 3 次，使用指数退避（1s、2s、4s）
- **且** 每次重试记录警告

#### 场景：永久故障时丢弃
- **当** webhook 端点在重试后返回 HTTP 401 时
- **则** 导出器记录错误并丢弃批（不阻塞管道）

### 需求：SyslogSIEMExporter
系统应提供一个 `SyslogSIEMExporter`，通过 TCP 或 UDP 带可选的 TLS 发送 SecurityEvent 作为 RFC 5424 syslog 消息。它应支持可配置的 facility 和严重级别映射。

#### 场景：TCP 传输
- **当** `SIEM_SYSLOG_PROTOCOL=tcp` 且事件被刷新时
- **则** 导出器打开到配置的 host:port 的 TCP 连接，并为每个事件发送一条 syslog 消息

#### 场景：UDP 传输
- **当** `SIEM_SYSLOG_PROTOCOL=udp` 且事件被刷新时
- **则** 导出器发送 UDP 数据报（无连接状态，即发即忘）

#### 场景：TLS 加密
- **当** `SIEM_SYSLOG_TLS=true` 时
- **则** 导出器将 TCP 连接包装在带证书验证的 TLS 中（可配置 CA 包）

#### 场景：RFC 5424 格式合规
- **当** 严重级别为 HIGH 的事件被导出时
- **则** syslog 消息使用 `facility * 8 + severity` 计算的 PRI，其中 severity 映射自 SecurityEvent 严重级别（critical=0、high=1...）

#### 场景：连接失败记录
- **当** syslog 服务器不可达时
- **则** 导出器记录错误，丢弃当前批，并在下次刷新时尝试重新连接

### 需求：OCSFFormatter
系统应提供一个 `OCSFFormatter`，将 SecurityEvent 映射到 OCSF v1.5 模式类。API 事件映射到 Activity class（4001），工具决策映射到 Authorization class（2201），发现结果映射到 Security Finding class（2001）。格式化器是一个转换层，包装另一个导出器（通常是 webhook）。

#### 场景：API 事件映射到 OCSF Activity
- **当** event_type="api" 的 SecurityEvent 被格式化时
- **则** 输出 JSON 包含 class_uid: 4001、activity_name、actor.user、time 和 severity_id

#### 场景：工具决策映射到 OCSF Authorization
- **当** event_type="tool_policy" 且 decision="DENY" 的 SecurityEvent 被格式化时
- **则** 输出 JSON 包含 class_uid: 2201、decision="deny"、action_id 和 actor 字段

#### 场景：发现结果映射到 OCSF Security Finding
- **当** event_type="anomaly" 的 SecurityEvent 被格式化时
- **则** 输出 JSON 包含 class_uid: 2001、finding_info、severity_id 和 resources

### 需求：SecurityEventCollector
系统应提供一个 `SecurityEventCollector`，订阅 AuditLog、ToolDecision 和 SecurityFinding 事件流。收集器将事件归一化为 SecurityEvent，应用可配置过滤，并通过异步批量刷新路由到注册的 SIEMExporter。

#### 场景：从 AuditMiddleware 收集的事件
- **当** AuditMiddleware 为 `POST /api/agents` 产生事件时
- **则** 收集器将其归一化为 SecurityEvent 并缓冲

#### 场景：从 ToolDecisionEmitter 收集的事件
- **当** ToolDecisionEmitter 为工具 `bash` 产生 DENY 事件时
- **则** 收集器将其归一化为 severity HIGH 的 SecurityEvent 并缓冲

#### 场景：从 FindingEngine 收集的事件
- **当** FindingEngine 持久化一个 SecurityFinding 时
- **则** 收集器将其归一化为 SecurityEvent 并缓冲

#### 场景：事件类型过滤
- **当** `SIEM_FILTER_EVENT_TYPES=tool_policy,anomaly`（排除 API 事件）时
- **则** 收集器跳过 AuditLog 事件，仅处理 ToolDecision 和 SecurityFinding 事件

#### 场景：严重级别阈值过滤
- **当** `SIEM_MIN_SEVERITY=medium` 时
- **则** 收集器仅缓冲 severity MEDIUM、HIGH 或 CRITICAL 的事件
- **且** INFO 和 LOW 严重事件静默丢弃

#### 场景：批量刷新
- **当** 收集器的缓冲区达到 `SIEM_BATCH_SIZE` 事件时
- **则** 所有缓冲事件刷新到注册的导出器
- **且** 缓冲区被清除

#### 场景：基于时间的刷新
- **当** 自上次刷新后经过 `SIEM_FLUSH_INTERVAL` 秒时
- **则** 无论缓冲区大小如何，所有缓冲事件都被刷新

### 需求：SIEM 管道默认禁用
系统应默认 `SIEM_ENABLED=false`。禁用时，不启动收集器，不注册导出器，不收集或导出事件。管道启动和关闭应由应用程序生命周期管理。

#### 场景：禁用时无开销
- **当** `SIEM_ENABLED=false` 时
- **则** 不实例化 SecurityEventCollector
- **且** 不发生事件归一化或缓冲

#### 场景：启用时启动
- **当** `SIEM_ENABLED=true` 且应用程序启动时
- **则** 收集器初始化，注册已配置的导出器（webhook/syslog/ocsf），并开始收集事件

#### 场景：优雅关闭刷新缓冲区
- **当** 应用程序关闭时缓冲区中仍有事件
- **则** 收集器在关闭完成前刷新所有剩余事件
