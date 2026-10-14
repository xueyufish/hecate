# Proposal

## Why

step7 本地治理切片（#245，change `runtime-independent-governance`）交付了统一受控动作边界、本地 policy profile、可信时间锚与本地审批权威，但其"不含"清单把四项显式留给受管切片：受管授权租约的 policy profile 认证（签发者/受众/策略版本全量校验、断连最大陈旧窗口、真实时钟偏移、重连重验证）、平台 EventStore 迟到追加 fencing 缺口。当前代码事实：租约 `Claims` 无策略版本字段，`exp` 判定用裸墙钟（时钟回拨可延长受管授权），期望配置 `config_fingerprint` 是只写字段（重连不校验），`platform_events` 无 `(run, source_sequence)` 唯一约束且序号分配为无 fencing 的 `max+1`（并发分配可静默重位、迟到事件可静默落地）。`managed-runner-enrollment` 主规格已要求"profile SHALL 显式配置最大陈旧窗口"与"重连验证期望配置"，本 change 补齐实现与验收证据，使受管模式（I-Cb 里程碑）获得可认证的授权租约语义。

## What Changes

- **租约携带策略版本与签发时间**：`hecate-durable` 凭据契约的 `Claims` 增加可选 `policy_version` 与 `iat`（参与 HMAC 签名）；平台按 enrollment 的操作员配置签发携带二者的限域限时租约。旧租约（无新字段）保持可验证，但受管 profile 下缺失字段按"未声明"显式处理。
- **受管授权租约 profile（`ManagedLeaseProfile`）**：作为 `PolicyProfile` 的第二实现落在可独立依赖组件中（平台与宿主共用语义与测试）：全量校验签发者允许列表、受众（部署绑定）、主体/租户绑定、动作范围（scope）、策略版本（不一致即拒；旧版本租约不得越过本地已知拒绝）、期限、防重放 nonce。
- **最大陈旧窗口与可信时间**：profile 声明 `max_staleness_seconds`（与租约 TTL 分立）；离线有效期 = min(租约 `exp`, 签发 `iat` + 最大陈旧窗口)。租约期限与陈旧窗口判定接入既有 `TimeAnchor/validate_clock`——时钟回拨或时间不可信时，拒绝依赖该期限的新动作；断连不切换信任根、不本地自授权。
- **高风险动作断连规则**：受保护（写类）动作在断连时停止，除非存在"当前有效的本地审批绑定"（复用本地审批权威与绑定校验）；低风险（readonly）动作在陈旧窗口内按租约继续。断连期间中心撤权无法即时到达，由陈旧窗口上界约束。
- **重连期望配置验证**：宿主在凭据交换/pull 时上报自身配置指纹（runner profile 与已装载定义摘要）；平台与操作员刷新的 `config_fingerprint` 比对，不匹配则拒绝新受保护工作（证据上传与历史补传不受阻）；过期审批、旧授权与迟到命令不得重新激活动作（回归保持）。
- **平台事件位置 fencing**：`platform_events` 增加 `(workspace_id, run_issuer_domain, run_ref_id, source_sequence)` 唯一约束（expand/contract 迁移，不做破坏性 downgrade）；序号分配冲突显式报错，投影器对迟到事件的去重/重序处理显式化，消除并发 `max+1` 静默重位与迟到静默落地；治理事件写入方保持单一，终态投影不重开。

**本切片不含**（后续 change 承接，不在此静默丢弃）：Sandbox adapter 包装与旁路封闭攻击演练（受保护写动作默认 `write_enablement: disabled` 不变，本 change 不因 profile 交付而启用）；I-Cb 里程碑的其余组成（step11 中心发布、step14 平台最小视图、step16 完整断连故障集与 PostgreSQL 进程矩阵认证）；供应商账单对账与完整 reported/estimated/reconciled 报表（step10）；真实模型多轮自主执行验收（step5e）。**本 change 归档只代表受管授权租约 profile 切片完成，不代表 I-Cb 里程碑或 step7 整体完成。**

## Capabilities

### New Capabilities

（无——本 change 全部落在既有能力的需求变更上，不新建近重复能力。）

### Modified Capabilities

- `managed-runner-enrollment`：租约需求扩展签名字段集（policy_version/iat）、可信时间与陈旧窗口语义、高风险断连规则（在线判定或有效本地审批绑定）；重连需求落实期望配置指纹的比对与拒绝行为。
- `standalone-runner-host`：受管运行装配 `ManagedLeaseProfile`（`PolicyProfile` 第二实现）；生产租约校验集扩展策略版本、陈旧窗口与可信时间；宿主上报配置指纹。
- `platform-task-control`：治理事件 envelope 需求增加位置唯一性与写入方 fencing——序号分配冲突显式、迟到追加显式处理、并发写入不得产生同位静默重排。

## Impact

- 代码：`packages/hecate-durable/src/hecate_durable/contracts/credentials.py`（Claims 扩展）、`packages/hecate-governance/`（受管 profile 判定组件，纯数据+小接口）、`packages/hecate-runner/`（LeaseGate 可信时间与陈旧窗口、ManagedLeaseProfile 装配、配置指纹上报）、`src/hecate/execution/managed_channel.py` 与 `src/hecate/channel/api/managed.py`（签发扩展、期望配置比对）、`src/hecate/execution/governance_events.py`、`src/hecate/models/platform_event.py`、`src/hecate/execution/event_relay.py` 与 alembic 迁移（唯一约束与 fencing）；`openspec/specs` 对应三份主规格随归档同步。
- 兼容：既有租约签发/验证、nonce 重放、续租等待、命令幂等与事件 `event_id` 去重语义不回退；`Claims` 新字段可选、旧凭据可验证；`platform_events` 迁移须容忍历史同位数据（存在则先修复后加约束）；G1/step6 回归（SC04/SC05、lease gate、managed loop）全绿。
- 依赖：不新增第三方运行时依赖；复用 `hecate-governance` 可信时间组件与本地审批权威，不引入新 Runtime 扩展点（`PolicyProfile` 已有本地实现为第一实现，受管实现为第二实现）。
- 验收基线：断连到期/陈旧超窗后新受保护动作拒绝且零业务副作用；时钟回拨不延长受管授权；策略版本不匹配拒绝；重连期望配置不匹配拒绝新受保护工作但证据补传继续；高风险断连仅凭有效本地审批绑定放行；并发事件分配冲突显式、迟到事件不静默重位；既有受管回归全绿。
