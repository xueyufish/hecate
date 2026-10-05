# Tasks

## 1. 契约与信任根基础

- [x] 1.1 `trust_roots` 平台表 + alembic 迁移(命名引用、凭据材料摘要、workspace 隔离、撤销状态);`TrustRootRegistry` 服务(登记/撤销/解析,单写入方)。验证:模型与迁移测试;workspace 隔离负例。
- [x] 1.2 HMAC 凭据原语:挑战签发/应答验证、部署域 bearer 签发与轮换(`security-claims` 语义字段:iss/aud/sub/exp/nonce)。验证:签名/验证/过期/防重放单元测试。
- [x] 1.3 `TrustRootResolver` 实现 `enrollment_resolver` 协议,composition 装配进 `set_managed_opt_in` 链;解析失败/缺失保持待定并留审计。验证:准入链集成测试(可信解析通过/不可信阻断/撤销后阻断)。

## 2. 平台侧受管通道

- [x] 2.1 enrollment 管理 API:注册(携带挑战应答)、查询、准入与 `managed_new_runs` 切换(workspace 管理员权限)。验证:API 测试覆盖权限负例与审计行。
- [x] 2.2 宿主通道 API(`/managed/*` router,凭据验证依赖):拉取派发(delivery envelope 游标 + 新租约附带)、事件上传批接口(event_id 去重)、对账查询(enrollment+delivery_id)。验证:凭据验证/自报忽略/过期凭据拒绝测试。
- [ ] 2.3 受管派发目标:worker 派发回调扩展——admitted+managed enrollment 的任务意图落 outbox(`source=platform-managed`),undelivered 状态与超时对账。验证:投递→拉取→delivered 标记;响应丢失重发幂等测试。
- [x] 2.4 `ManagedProjectionService`:上传事件去重落 `platform_events` + Run 投影更新(只追加/只更新平台投影,无宿主回写路径)。验证:重复批次幂等;投影与本地事实冲突时平台侧如实记录。

## 3. 宿主侧受管 profile

- [x] 3.1 `RunnerConfig.control_plane` 可选段(地址/凭据引用/租约 TTL/间隔/陈旧窗口);缺失时零装配、零行为变化。验证:无受管段的既有 runner 测试全绿(回归红线)。
- [x] 3.2 `hecate_runner/managed.py`:注册(HMAC 挑战)、心跳/拉取循环、租约 `LeaseGate`(签发者/受众/部署/exp/nonce 验证 + 本地 nonce 消费表)。验证:过期租约拒新受保护动作、无本地自授权路径、旧 nonce 重放拒绝。
- [x] 3.3 接受记录与执行:delivery → `submit_task` 幂等接受 → 本地 durable 队列;重复 delivery 幂等返回;执行事实沿既有 durable 语义(断连继续本地执行)。验证:重复拉取不重复执行;断连后本地任务完成。
- [x] 3.4 事件上传:本地 `durable_event_log` 游标批量推送,重连先补传再请求新 delivery。验证:断连期间事件重连后无丢失无重复(平台侧断言)。

## 4. 重连与治理边界

- [x] 4.1 重连重验链:凭据 → enrollment ADMITTED+managed → 信任根摘要一致 → 期望配置指纹 → 新租约;撤销后 403 停止派发。验证:撤销阻断、信任根变更阻断、正常重连恢复。
- [x] 4.2 迟到/重复治理:重复命令 command_id 幂等;过期审批/旧授权/迟到命令不激活动作(状态机既有语义 + 租约门);活跃 Run 不迁移断言。验证:重复命令与旧 ownership 注入测试。

## 5. 场景与收尾

- [x] 5.1 SC05 场景实现并翻转 `implemented`(manifest + 场景测试,四断言全覆盖:重验后接受新工作、历史事件去重补传、重复命令不重复业务动作、投影不覆盖本地事实)。验证:场景测试绿;manifest 一致性测试通过。
- [x] 5.2 文档与方案同步:`hecate-runner/README` 受管段、部署文档(受管叠加配置)、演进方案 step6 末项勾选 + step7 受管三项注记 + SC04 部分覆盖注记(完整翻转归 step7 剩余)。验证:文档与交付一致。
- [x] 5.3 全量验证:`ruff check/format`、`mypy src/ packages/`、scoped pytest(runner 包 + test_execution + test_channel + scenarios)全绿;CI 通过。验证:本地门禁零错误。
