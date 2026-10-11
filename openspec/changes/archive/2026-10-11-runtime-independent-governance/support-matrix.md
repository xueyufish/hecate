# step7 本地治理切片 — 部署信任与支持矩阵

本文件是 change `runtime-independent-governance` 的支持矩阵记录(tasks 6.2)。

## 已验证范围(本 change 交付)

| 部署组合 | 治理等级 | 启用的能力 | 证据 |
|---|---|---|---|
| standalone durable profile + 本地信任材料(identity.json/租约密钥)+ 本地 governance 配置 | 标准(本地切片) | 全工具(含 readonly)统一授权判定;本地审批权威(登记审批主体签发、durable 持久化、一次性消费/撤销/期限+可信时间);本地预算预留/结算(`governance.budget_limit`显式启用);凭据引用化 + 业务 API 服务身份头 | `packages/hecate-runner/tests/test_local_governance.py`、`test_dispatch_governance.py`、`test_budget.py`、`tests/scenarios/test_sc03_local_governance.py`、`test_sc03_local_approval_restart.py`(真实 wheel / 进程重启) |
| 平台控制面(builtin/聊天/Gateway/REST/MCP/A2A)+ SQLite 测试后端 | 标准(平台侧) | 统一受控动作判定(audit 默认,可 enforced/off)、审批权威表 + 投影分离、预算预留、身份信封贯通、参数级 DENY 跨入口一致 | `tests/test_core/test_governance_repairs.py`(独立连接 SQLite 文件库并发/回滚)、`tests/test_core/test_governance_*.py`、`tests/test_enterprise/test_governance_authority.py`、`tests/test_tools/test_gateway/test_param_rules_parity.py` |
| 受管运行(经租约门) | 继承 step6b | 租约门先于本判定,行为不回退 | `packages/hecate-runner/tests/test_lease_gate.py`、`test_managed_profile.py`、`tests/scenarios/test_sc04_lease_renewal_policy.py`、`test_sc05_managed_reconnect.py`、`test_sc05_managed_wake_chain.py`(真实进程回归) |

## 较低治理等级(实验范围)

| 部署组合 | 等级 | 边界 |
|---|---|---|
| 本地 profile + `governance.write_enablement: "experimental"` + 业务 API attestation | 实验 | 仅覆盖安全不依赖 Hecate 旁路封闭、由业务 API 独立强制最终授权的写动作;不计入依赖强制保证动作的认证;attestation 必填,缺失时 profile 加载失败 |

## Review 修复后的运行边界

- Runner 启动时必须装配本地 policy profile,无授权规则时默认拒绝工具;受保护写动作仍需显式实验启用及业务 API attestation。
- 同一参数的新动作使用新 invocation ID;需要重试/重放的客户端必须保持调用 ID。Gateway 使用 `x-hecate-invocation-id`,并按已验证主体和 workspace 隔离;未提供时每次分配新 ID。
- 平台 claim/审批消费/预算预留通过独立短事务提交;请求事务回滚不撤销已提交占位。一次动作只由一套台账领取。
- `resume` 的本地 HTTP 请求通过 `approval_ref`,受管命令通过 `detail_ns.approval_ref`传递引用;引用只作查询句柄,仍需权威记录的主体、参数、资源、任务范围与登记审批主体校验。
- 有期限的审批持久化墙钟、单调钟和进程 epoch。进程变化后若无法证明时间连续性,新派发拒绝该审批;需要重新签发。无期限审批及已完成回执重放不受该限制影响。
- 平台模型 token 按现有成本估算换算至 workspace cost quota;Runner 本地额度按模型 token 单位计量。结果未知时保留预留和 unknown 状态,明确结算后才释放差额;供应商账单对账仍在后续切片。

## 未验证项 / 明确不支持(本切片)

- **真实 PostgreSQL 后端的本轮验证**:本轮 SQL 并发/回滚验收使用独立连接 SQLite 文件库;不将 ORM 方言兼容性或 SQLite 测试冒充 PostgreSQL 进程认证。
- **受管授权租约的 policy profile 认证**:签发者/受众/策略版本全量校验、断连最大陈旧窗口、真实时钟偏移窗口、重连重验证 → I-Cb 后续 change(依赖本切片的绑定/可信时间组件)。
- **旁路封闭完整验证**(Sandbox 包装、出口绑定、攻击演练)→ 后续切片;在此之前受保护写动作默认关闭(`write_enablement: "disabled"`),运维留痕不构成启用依据。
- **真实模型多轮自主执行验收** → step5e(本 change 不冒充)。
- **完全隔离网络 profile** → step11/16 单独认证。
- **供应商账单对账、完整 reported/estimated/reconciled 报表** → step10(本切片提供 invocation 级预留/结算/未知账原语)。
- **平台 EventStore 迟到追加 fencing 缺口** → 随受管切片修复;本切片未触碰。

## 模式开关语义(记录)

- `GOVERNANCE_MODE=audit`(默认):判定全部落日志;安全底线(已知撤销、策略要求审批而缺少有效绑定、身份缺失、证据不可写)在 audit/enforced/off 三模式下均生效。
- 回退(off)不复活已消费审批、不绕过底线;可灰度的只有策略差异(阈值/宽严)。
- 授权判定逐动作执行,无结果缓存(跨任务授权不同的漏判风险,D6 风险条目)。
