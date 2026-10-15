# Support Matrix — managed-lease-profile

受管授权租约 profile 切片（step7 I-Cb 的 step7 组成）交付边界与未验证项。依据演进方案 step7（计划 401/415/416 行操作项的受管侧）与 step6 的 EventStore fencing 跟踪项；归档本 change 只代表本切片完成，不代表 I-Cb 里程碑或 step7 整体完成。

## 已交付（本切片验证）

| 能力 | 验证证据 | 状态 |
|---|---|---|
| 租约签名集扩展（`policy_version`/`iat` 参与 HMAC） | `tests/test_execution/test_managed_enrollment.py`（签名防篡改、旧令牌兼容、往返） | implemented |
| 受管租约 profile（`hecate_governance.managed`）：事实门、可信时间窗、版本声明执法、撤销合并 | `packages/hecate-governance/tests/test_managed_profile.py`（21 例）；local/managed 规则一致性 `packages/hecate-runner/tests/test_managed_profile_contract.py` | implemented |
| 租约门可信时间与陈旧窗口（`min(exp, iat+window)`、时钟回拨拒绝、重启重锚、结构性立即拒绝） | `packages/hecate-runner/tests/test_lease_gate.py`（19 例） | implemented |
| 引擎审批优先断连桥（有效本地审批绑定放行、不消耗租约 nonce、证据记录 `local_approval` 来源） | `packages/hecate-runner/tests/test_engine.py`（审批桥 + 结构性拒绝不烧等待预算） | implemented |
| 平台签发携带 enrollment posture 的策略版本；操作员 `lease_policy` 列与 opt-in 门（网络事件不可变更） | `tests/test_execution/test_managed_enrollment.py`（存储、校验、签发） | implemented |
| 重连期望配置门：宿主指纹上报、pull/accept 比对、不匹配拒新工作且证据补传不受阻、未装备不启用 | `tests/test_execution/test_managed_channel.py`（HTTP 级）；SC05 场景 `test_sc05_config_drift_stops_new_work_evidence_upload_continues` | implemented |
| 平台事件位置 fencing：`(workspace, run, source_sequence)` 唯一约束、迁移同位修复（保留最早行、记录被移除 event_id）、并发分配收敛（savepoint + 去重/重试）、迟到重排、孔洞游标语义 | `tests/test_execution/test_event_position_fencing.py`（4 例，SQLite）；迁移修复 `tests/test_migrations/test_platform_event_position_repair.py`（2 例） | implemented（SQLite）；PostgreSQL 并发见未验证项 |

## 未验证项 / 明确不支持（本切片）

- **真实时钟偏移与断连窗口的实钟认证**：陈旧窗口/回拨判定以注入时钟验证确定性分支；真实时钟偏移（NINT 漂移、断电后 CMOS 回退）的实钟认证归 step16 断连故障集。
- **PostgreSQL 并发下的位置 fencing**：唯一约束迁移与冲突收敛已验证于 SQLite；PostgreSQL 进程级并发（多 worker 同时分配）按既有条件模式（`HECATE_STEP6_POSTGRES_URL`）在 CI 的 PG 作业中执行，本地未运行不冒充已认证。
- **配置指纹的完整性边界**：指纹只捕获配置漂移（过期配置、未升级定义）；不构成宿主完整性证明——防恶意宿主由信任根材料与准入链负责，本门不扩展该声明。
- **受保护写动作保持 `write_enablement: disabled`**：本切片交付授权语义，不因 profile 交付启用受保护写；旁路封闭（Sandbox 包装、出口绑定、攻击演练）归后续切片。
- **I-Cb 里程碑其余组成**：step11 中心发布、step14 平台最小视图、step16 断连故障集与全场景矩阵认证均未在本切片范围内。
- **供应商账单对账 / reported-estimated-reconciled 报表**：归 step10。

## 模式开关语义（记录）

- 受管 profile 随 `control_plane` 装配；`lease_policy` 块缺省时事实门与窗口仍然生效（断连保护不回退），版本严格性仅在操作员声明期望版本后执法——step6 既有装配（无该块）行为保持。
- 陈旧窗口双侧各自配置、各自执法（`min(exp, iat+本侧窗口)`），任一侧更短即更早拒绝，fail-closed，无协商协议。
- 期望配置门由操作员显式 `refresh_config_fingerprint` 装备；仅装备前的登记指纹不触发比对（既有信任根登记兼容）。
- 宿主收到 config-drift 拒绝：记录漂移原因与计数（`last_config_drift`/`stats.config_drift_rejections`）、继续 poll；成功调用清除标记。不做本地模式切换。
