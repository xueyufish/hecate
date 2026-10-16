# Tasks

## 1. 方案修订

- [x] 1.1 改写总体平台边界与团队验收，移除指定工单流程；通过 diff 确认保留交接、产物、幂等、重启和撤权要求。
- [x] 1.2 补充审批、输出 Schema、恢复场景的条件适用与合成 fixture 边界；核对 step5e 和场景门槛不再暗示所有任务执行固定链路。

## 2. 验证

- [x] 2.1 复核 diff、步骤与状态未变、历史 fixture/报告未改；运行 git diff --check 和 OpenSpec strict validate，并在 verification.md 记录结果。
