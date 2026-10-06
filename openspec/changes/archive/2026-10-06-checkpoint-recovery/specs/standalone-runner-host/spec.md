# Spec Delta

## ADDED Requirements

### Requirement: 中断任务从持久 checkpoint 恢复原逻辑执行

durable profile 的宿主 SHALL 把执行 checkpoint 持久化到宿主自有存储(superstep 粒度),并以任务级稳定会话标识关联同一任务的全部尝试。重启后恢复非终态任务时,宿主 SHALL 优先从最近 checkpoint 恢复原逻辑执行(从崩溃 superstep 继续),checkpoint 缺失或不可用时回退到既有 replay 语义——回退 MUST NOT 改变动作台账的仲裁结果。恢复执行 SHALL 在与中断尝试相同的 attempt 上继续(不新建 Run);已决动作(succeeded)经台账回填真实结果,`outcome_unknown`/claimed 写类仍停止待对账。恢复路径 MUST NOT 重做任何已记录真实结果的业务副作用(以业务 API 调用计数验证)。

#### Scenario: 落盘后崩溃从崩溃点继续且不重做已决动作

- **WHEN** durable 任务执行到中途(前序保护动作已 succeeded 落账)进程崩溃,重启后调度恢复
- **THEN** 执行从最近 checkpoint 的 superstep 继续,前序已决动作回填真实结果,业务 API 对这些动作零重复调用,任务收敛原终态语义

#### Scenario: checkpoint 缺失回退 replay

- **WHEN** 恢复时该任务会话无可用的持久 checkpoint
- **THEN** 宿主按既有全图 replay 恢复,动作台账逐工具仲裁,安全语义与现状一致
