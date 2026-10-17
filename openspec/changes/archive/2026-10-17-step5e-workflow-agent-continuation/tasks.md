# Tasks — step5e-workflow-agent-continuation

## 1. 声明与盖章

- [x] 1.1 `WorkflowPlan` 步骤字段支持 `approval_wait`(布尔校验,非法显式拒绝),`stamp_step_input` 将声明盖章进子任务输入;验证:计划解析测试(旧格式兼容/合法受理/非法拒绝/盖章形状)全绿,既有子任务契约测试不改断言全绿

## 2. 系统快照归属

- [x] 2.1 实现 `system_execution_tenant(agent_id)` 派生函数(uuid5 独立命名空间),保存侧 `execution_assembly` 在 `user_id is None` 且有服务端 agent 归属时启用合成键 tenant provider,恢复侧 `_resume_engine_session` 同源推导;两者皆无时维持现状(跳过持久化);验证:保存/恢复同键单测 + 归属缺失/推导不一致拒绝恢复反例 + 带人类用户路径回归不变

## 3. 拒批收敛父流程

- [x] 3.1 `_apply_approval_rejected` 终态后检测 `workflow_parent` 盖章,经既有 `_issue_child_callback` 核实路径发出失败回调(来源 approval_rejected 可区分);验证:on_failure=fail 与 await 两分支确定性测试(父不等到超时、来源可归因)+ 子 waiting_approval 期间伪造成功回调被既有核实拒绝的反例

## 4. 确定性组合套件(SQLite)

- [x] 4.1 组合场景:两步 workflow(步骤声明 approval_wait+产物)→ 子停车 → 父保持等待(状态与等待契约不变)→ 批准续跑 → 产物校验通过 → 父推进恰一步;拒批分支按 3.1 收敛;验证:上述全链 + 副作用恰一次断言全绿,既有持久审批套件(25)与 workflow 子任务套件不改断言全绿

## 5. PG 组合重启 E2E

- [x] 5.1 双窗口子进程重启 E2E(复用 #261 worker 子进程框架):(a) 子停车时 kill 子 worker,审批续跑后父推进;(b) 父等待中 kill 父子进程再拉起,关联/审批/回执/流程位置从 PG 恢复;断言副作用恰一次、绑定消费一次、父推进恰一步;验证:PG 专用模块经 `HECATE_STEP6_POSTGRES_URL` 实跑通过,无 URL 显式 skip
- [x] 5.2 CI PG 作业纳入新模块(既有 hecate-runner 安装步骤已在);验证:CI diff 审查 + 本地按 CI 命令复跑

## 6. 真实模型与文档

- [ ] 6.1 真实模型变体(env 门控,机械断言不变,措辞只记录):子 Agent 自主发起受保护调用 → 停车 → 进程重启 → 批准 → 续跑 → 产物校验 → 父推进;验证:带时间戳记录落盘(gate=false)或凭据缺失显式 skip
- [x] 6.2 文档如实更新:演进方案 step5e 状态行、执行机制矩阵、ADR-036;feature-catalog 登记 1.3.11k 并同步 inventory(extract → 填治理字段 → sync 托管区 → `check --base-ref` exit 0,完整退出码校验);验证:文档 diff 审查 + inventory check 干净

6.1 复核状态：原摘要保留。复核真实模型 v2 运行在审批/父子重启与单次副作用上通过，但最终子输出非 JSON，父按声明 Schema 正确转 validation 失败，未完成“产物校验通过后父推进”的成功验收。失败原始记录保留；不得勾选该项。补字段化结果的模型质量/输出归一化方案另行明确后再验收，不通过重复采样或放宽断言宣告通过。

## 7. 验证收口

- [x] 7.1 静态门:`ruff check/format` 零错误、`mypy src/` 及三个 runtime 包零错误、`openspec validate --strict` 通过
- [x] 7.2 定向回归:新增模块 + 受影响域(execution/runtime/workflow 子任务/审批/task-control)既有测试全绿;PG 专项实跑并清理临时容器
- [x] 7.3 宽回归:channel/execution/runtime/durable/governance/enterprise/workflow/MCP/S12/SC03 通过零失败;撰写 verification.md(命令、结果、跳过项如实、实施修正记录)

## 8. Codex 复核修正

- [x] 8.1 修正非法审批声明与系统快照键规范化，补生产保存侧及归属拒绝反例
- [x] 8.2 拒批终态的父回调接入已有 outbox 重放；统一自动回调请求形状，覆盖回调失败后重试与去重
- [x] 8.3 补齐真实进程窗口、结果/回执/审批身份断言及真实模型失败记录，清理无效测试断言
- [x] 8.4 运行共享检查、受影响测试及 PostgreSQL 专项，更新实际复核记录
