# Tasks

## 4. Codex 复核修正

- [x] 4.1 输出规范化仅作用于声明 Schema 的结果，HTTP direct/engine、同步/流式一致；普通 Markdown 保留原文，补实际入口反例。
- [x] 4.2 基线采集保留逐轮输入/输出/usage、工具回执、审批决定及异常前事实；凭据选择、空 usage、评分和阈值 Schema 可靠；新 rubric 不覆盖旧实测记录。
- [x] 4.3 修复同用户同 Agent 的真实上下文恢复，补确定性入口证据；真实模型断连测试使用持久快照和新会话读取，断言恢复事实/产物而非仅打印，完整清理全局装配和服务器。
- [x] 4.4 执行确定性与 PG 回归，按可用凭据保存新的实际模型记录；如实更新设计、验收状态与复核文档，保留默认关闭。

## 1. 采集器扩展与预注册

- [x] 1.1 rubric v3:阈值常量沿用并固化;`_score` 增加 `feedback_params_exact`(ticket_body 逐字)独立评分项;报告 `_meta` 增加 `cost_threshold_conclusion`,`unverified` 清单更新(移除真模型审批/恢复项,保留 Runner/业务价值/放量);报告 schema 校验 thresholds 与实际一致。
- [x] 1.2 新案例 `approval_resume`:`StubBusinessRegistry` 审批语义(先拒后批),direct 同步回调与 engine ToolWorker 审批回调共用同一确定性权威;评分 `approval_resume_compliance`(批准路径产物合规、拒批零副作用、审批事件留痕)。
- [x] 1.3 采集器自测(注入 completion 双打,不调真模型):双路径 × 5 案例全绿,skipped 分支与报告 schema 校验保持;既有报告测试同步更新。

## 2. 真实模型运行与记录

- [x] 2.1 以真实凭据运行采集器(`BASELINE_MODEL` 默认 gpt-4o),产出 recorded 报告替换 skipped 记录;逐案例核对评分、延迟、token;失败案例如实记录不重试刷分。
- [x] 2.2 断连恢复场景模块 `tests/scenarios/test_real_model_disconnect.py`:G3 harness 模式 + 真实 provider + 合成工具;断连后恢复零重复、真实回执回填、结构化产物合规、持久事实新会话可查;SQLite 运行 + PG 参数化;凭据缺失显式 skip。
- [x] 2.3 实测暴露缺陷分流:注入双打可复现的机制级缺陷在本 change 修正并附确定性回归;模型行为质量级偏差记录进报告(调参需新 rubric 版本,不刷分)。

## 3. 文档定稿与验证

- [x] 3.1 机制矩阵:真实记录行/证据节更新(recorded 报告 + 断连恢复场景引用),剩余门禁更新(Runner 选工具、宿主持久审批、独立 workflow 身份、放量)。
- [x] 3.2 ADR-036:Status 更新为 real-model baseline recorded;增补双路径实测结论与缺陷修正记录;"不授权默认切换"结论不变。
- [x] 3.3 方案 step5e 勾选项按方案原文评估更新(记录级验收 vs 生产启用的依赖区分),verification.md 记录实际模型运行凭据(运行命令、模型版本、报告路径);确定性回归 + ruff + mypy + strict validate 全绿。
