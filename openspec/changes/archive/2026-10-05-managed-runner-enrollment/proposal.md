# Proposal

## Why

演进方案 step6 仅剩"受管投递与状态投影"未勾,step7 的受管切片(限时授权租约、断连边界、重连验证)以其为前置;两者按方案的 change 拆分表共同归属本 change,是 I-Cb(受管任务闭环)迭代的启动工作。底座已全部就绪:step4 交付了 enrollment 模型与操作员准入(`StandaloneEnrollmentModel` + `TaskRunRegistry`,`enrollment_resolver` 注入点空置),step6 worker 交付了双端共享的 `hecate-durable`(租约/fencing、事件日志 event_id 去重、命令 command_id 幂等),step3 交付了 `security-claims` 契约(iss/aud/sub/tenant/delegation_ref/exp)。当前缺口是:可信解析器未装配、宿主没有控制面通道、平台没有宿主执行事实的持久投影、断连/重连语义未实现——SC05(重连与重复控制命令,登记 4/6/7)因此仍为 `planned`。

## What Changes

- **受管通道(宿主侧)**:hecate-runner 增加可选的控制面客户端——按配置的平台地址注册(enrollment)、心跳、拉取派发意图与控制命令、按游标上传执行事实/事件;通道认证使用部署域内短期凭据,客户端自报身份不产生任何权限。
- **受管投递(平台侧)**:已准入且 `managed_new_runs` 的 enrollment 成为平台派发的新目标——平台将任务意图写入投递 outbox,宿主以本地事务记录接受记录(intent/claim),提交响应丢失时按幂等键与已接受记录对账,不启动同义执行;调度所有权经租约 fencing,第一版不迁移活跃 Run。
- **状态投影(平台侧)**:宿主上传的执行事实/事件按 event_id 去重落为平台 Run 投影与治理事件(游标续传),投影永不覆盖宿主本地执行事实——每个字段一个权威写入方。
- **授权租约(step7 受管切片)**:平台按 `security-claims` 语义签发限域限时租约(iss/aud/部署/主体/期限/防重放 nonce);宿主验证签发者、受众、部署绑定与期限后接受新工作;断连后租约到期,新受保护动作停止,**不切换本地自授权**;最大陈旧窗口与过期拒绝为 profile 显式配置。
- **重连协议**:重连先重新验证 enrollment 仍准入、信任根未变更、撤销与期望配置,再允许新受保护动作;历史事件按游标去重补传;重复命令经 command_id 幂等;过期审批、旧授权、迟到命令不重新激活动作;信任根更新与退出受管模式仅经显式管理员流程。
- **可信解析器装配**:workspace 级信任根登记(命名引用解析到已登记的信任材料),`enrollment_resolver` 真实实现接入 `set_managed_opt_in` 准入链。
- **SC05 翻转**:场景测试实现并翻转为 `implemented`(受管模式全部四断言);SC04(断连授权过期)的租约到期语义随之可用,但其完整翻转仍归 step7 剩余切片。

## Capabilities

### New Capabilities

- `managed-runner-enrollment`:受管宿主闭环——enrollment 注册与操作员准入(含可信解析)、受管投递与接受记录对账、执行事实投影单写方、限时授权租约与断连边界、重连验证与去重补传;平台与宿主各交付一半,契约共享。

### Modified Capabilities

- `standalone-runner-host`:宿主新增可选受管 profile——控制面客户端(注册/心跳/拉取/上传)、租约验证门、断连不降级为本地自授权、重连重验信任后恢复;未配置平台地址时行为与现状完全一致(独立模式不受影响)。

## Impact

- `packages/hecate-runner`:新增 `managed.py`(控制面客户端、租约验证、接受记录、上传游标)与配置面(`RunnerConfig` 增加可选平台地址/凭据引用/陈旧窗口);server/engine 不改独立模式路径。
- `packages/hecate-durable`:宿主侧接受记录与上传游标复用既有表(命令幂等、事件日志),预计仅需少量查询辅助,不改契约与接缝。
- `src/hecate/execution/`:enrollment 解析器实现与装配(composition)、受管派发目标解析(worker 投递回调扩展)、宿主事件投影入口(复用 event_relay 语义,方向反转)。
- `src/hecate/channel/api/`:enrollment 管理 API(注册/准入/列表)与宿主通道 API(派发拉取、事件上传、租约签发)——通道认证走部署域凭据,不走用户会话。
- 迁移:enrollment 投递 outbox/租约如需平台侧表,alembic 增量(不改既有表所有权)。
- 测试:SC05 场景实现与翻转、宿主-平台双端集成测试(断连/重连/重复命令/旧 ownership)、enrollment 准入链测试。
- 明确不交付:策略引擎、审批语义、凭据代理、预算记账(G4)、SC04 完整翻转、受管生产认证(归 step7 剩余与 step16)。
