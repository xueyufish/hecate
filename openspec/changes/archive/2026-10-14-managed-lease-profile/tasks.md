# Tasks

## 1. 契约与受管 profile 组件（纯增量）

- [x] 1.1 `hecate_durable.contracts.credentials.Claims` 增加可选 `policy_version`/`iat`（`to_dict`/`from_dict` 按 None 省略），`lease_claims()` 接受 `policy_version` 并写入签发时刻 `iat`；验证旧令牌 HMAC 兼容与新字段往返的单测通过（`packages/hecate-durable/tests/`）。
- [x] 1.2 新增 `hecate_governance.managed`：`ManagedLeaseFacts`、`ManagedLeasePolicyConfig`、`ManagedLeaseProfile(PolicyProfile)`、`ManagedRevocationView`——受管门判定（租约事实缺失/超窗/策略版本不匹配/时间不可信拒绝受保护动作，组合本地规则评估，撤销合并两源）；单测覆盖策略版本不一致、陈旧超窗、时间不可信、撤销、readonly 不收紧（`packages/hecate-governance/tests/test_managed_profile.py`）。
- [x] 1.3 共享契约测试：同一输入下 local/managed 两 profile 的工具级规则判定一致、floor 由 `GovernanceService` 统一保证的断言通过。

## 2. 平台签发与重连期望配置门

- [x] 2.1 `StandaloneEnrollmentModel` 新增 `lease_policy` JSON 列（操作员受控，默认空）+ alembic expand 迁移；操作员 enrollment/opt-in API 接受 `lease_policy`，网络事件不可变更；迁移与 API 单测通过。
- [x] 2.2 `issue_lease_for` 把 enrollment 的 `policy_version` 写入租约（`iat` 随签发）；`tests/test_execution/test_managed_channel.py` 断言签发令牌携带二者。
- [x] 2.3 pull/accept 边界期望配置比对：宿主请求携带配置指纹，平台与 `TrustRootRegistry` 登记的 `config_fingerprint` 比对，不匹配拒绝 pull/accept 并返回显式错误；`/managed/host/events` 与 effect 回执上传不做该比对；真实 HTTP 测试覆盖"不匹配拒新工作、事件补传继续"与"未登记期望值不启用该门"。

## 3. Runner 租约门与受管装配

- [x] 3.1 `LeaseGate` 接入可信时间：构造函数接受 `clock`，`update()` 安装时打 `TimeAnchor`，`check()` 在 `exp`/`iat + max_staleness` 判定前 `validate_clock`，回拨/不可信拒绝且原因区分；重启后门空、新租约重建锚的行为有测试（扩展 `packages/hecate-runner/tests/test_lease_gate.py`）。
- [x] 3.2 `ControlPlaneConfig` 的 `control_plane` 块新增 `lease_policy`（期望策略版本 + 本地最大陈旧窗口）；`LeaseGate.check()` 执行 `min(exp, iat + 窗口)` 与策略版本一致性校验（不一致立即拒、不进续租等待）；配置缺失时的默认行为（未声明窗口按仅 `exp`、未声明版本按拒绝受保护）有测试。
- [x] 3.3 引擎受保护动作授权序列插入本地审批绑定优先查询（复用 `ApprovalGate`/绑定校验，授权来源 `local_approval` 不消耗 nonce，证据记录路径来源）；断连超窗 + 有效绑定放行、同批次无绑定拒绝、绑定一次性消费的测试通过（引擎/派发治理测试）。
- [x] 3.4 `profile.py` 增加配置指纹计算（去 secret 规范 JSON + 定义摘要 SHA-256），`ManagedChannel` 凭据交换与 pull 携带；指纹不受请求参数影响的测试通过。
- [x] 3.5 `__main__` 受管装配：`control_plane` 已配置时以 `ManagedLeaseProfile` 装配 `GovernanceService`（复用本地规则配置），`LocalPolicyProfile` 路径不回退；装配单测（managed CLI 缺一即失败语义扩展到 lease_policy 必填项）通过。

## 4. 平台事件位置 fencing

- [x] 4.1 alembic 迁移：修复同位重复数据（保留最早主键行、记录被移除 `event_id`；无法确定性修复则失败）后创建 `(workspace_id, run_issuer_domain, run_ref_id, source_sequence)` 唯一约束；含重复数据构造的迁移测试通过。
- [x] 4.2 `PlatformEventService.emit`/`append_resequenced` 冲突收敛：`IntegrityError` → savepoint 回滚 → 按 `event_id` 幂等跳过或有界重试新序号；并发分配恰好一条落库、迟到事件重排落库且游标读取不跳过/不重复的测试通过（`tests/test_execution/test_event_relay_projection.py` 扩展 + 新增并发用例）。
- [x] 4.3 确认平台事件游标分页对序号孔洞的语义并在事件 API 文档/docstring 明确（到达序标签，孔洞不表示丢失）；文档交叉引用检查通过。

## 5. 场景、回归与收尾

- [x] 5.1 SC05 扩展用例：受管断连超窗——无绑定受保护动作拒绝且零业务副作用、有效本地审批绑定放行、重连后期望配置不匹配拒绝新工作但补传完成（`tests/scenarios/`，manifest 状态如实登记）。
- [x] 5.2 既有回归全绿：G1 回归、SC03/SC04/SC05（含 wake chain）、`test_lease_gate.py` 全量、managed loop、`event_id` 去重与投影回归；`ruff check`、`ruff format --check`、`mypy src/`（含三个包）零错误。
- [x] 5.3 支持矩阵与文档：support-matrix 登记本切片交付边界与未验证项（I-Cb 其余组成、旁路封闭、PostgreSQL 进程矩阵条件跳过、指纹非完整性证明）；演进方案 step7 状态段与 `managed-runner-enrollment`/`standalone-runner-host`/`platform-task-control` 主规格同步说明在归档时执行。
