# Verification

> 以下为 GLM 初验历史记录。Codex 复核已发现采集完整性、围栏输出以及恢复验收问题，并在本 change 内修正。最终状态与新 rubric 观测以 [review.md](review.md) 为准；原“断连恢复通过”不证明原设计的持久快照、回执上下文与正确产物要求。

## Scope

基线 `a9364a34`。四组交付:rubric v3 采集器扩展(approval_resume 案例 + 成本三态 + 反馈参数独立评分)、真实模型基线 recorded、真模型断连恢复场景(PG)、机制矩阵与 ADR-036 定稿。代码面:`tests/scenarios/baseline/run_autonomous_baseline.py`(扩展)、`tests/scenarios/test_real_model_disconnect.py`(新增)、`src/hecate/channel/api/v1/chat.py`(实测暴露的机制缺陷修复:围栏 JSON 剥离)、`tests/test_channel/test_loop_stop_semantics.py`(回归)、`tests/scenarios/test_autonomous_baseline_report.py`(自测)、文档三件。

## Results

### 1. 采集器扩展(确定性自测,无真模型)

- rubric v3(`autonomous-execution-rubric-3`):`feedback_params_exact` 独立评分;`cost_threshold_conclusion: unverified` 进 schema;`unverified` 清单移除真模型审批/恢复项。
- `approval_resume` 案例:先拒后批的确定性权威。实现迭代:手搓回调被治理绑定校验连续拒绝("no approval binding provided"→"task scope differs"),改为**复用生产 `AuthorityBackedApprovalCallback` + `MemoryApprovalLedger`**(同 identity、同 session、access 层 `ToolRule ASK` + 治理管线双层)——镜像 step7 生产语义。注入 completion 自测 3 passed(双路径 × 5 案例)。

### 2. 真实模型基线(recorded)

- 运行:`BASELINE_MODEL=zai/glm-4.5-flash`(免费层),`OPENAI_API_KEY`/`ANTHROPIC_API_KEY` 在 `.env` 中为空值(键名存在但无值),ZAI 有值但 glm-4.6 余额不足(429 1113),glm-4.7-flash 免费层持续 "service overloaded"(8 次重试 + 60s 节流仍 10/10 限流;其单案例冒烟质量项全过、延迟超限——作为补充观察记录)。
- 最终 recorded 报告(0 采集错误,10/10):**direct 路径 5/5 全绿**——normal 全项通过、approval_resume 全项通过(含 `approval_resume_compliance`)、三类停止类别正确且副作用计数一致、延迟与 token 在阈值内。**engine 路径**:停止类案例全部正确;normal 在围栏修复后全绿;approval_resume 记录一次模型行为变异(首次被拒后未重提写工具,effects=0,安全零重复不受影响——质量级,如实记录不重试);多轮 run 延迟超 60s 阈值(免费层特征,如实记录)。
- **成本**:全程 unknown(`cost_amount: null`、`price_version: unverified`、`cost_threshold_conclusion: unverified`),未以估算冒充。

### 3. 真模型断连恢复(PG 层,1 passed)

- harness 复用 G3 模式(uvicorn 真实 socket + RST 中途断连 + 执行器内确定性 park),provider 为真实模型;NullPool/专池/WAL 来自 G3 的跨 loop 与池毒化教训(本模块再次实证 registry 池跨 loop 失败)。
- 安全断言(确定性):断连 run 收敛 cancelled/failed、工具回执恰一次持久、恢复零重复执行——全部通过。
- **记录的真实发现**:恢复请求的模型上下文未包含前序交换(模型回答 "I don't see any previous computation in our conversation",插桩取证);跨请求续推的上下文恢复范围是机制缺口,归入恢复跟进 change(与 G3 多轮连续性在 ASGI/SQLite 下的通过路径的差异有待归因)。回执本身从持久日志可答(日志回填机制在先)。

### 4. 实测暴露的机制缺陷(已修 + 确定性回归)

- **围栏 JSON**:真模型常用 ```json 围栏包裹答案,`_validate_output_schema` 直接 `json.loads` 失败——engine normal 案例的 invalid_output 即此。修复:剥离单层围栏(校验 + enforcement 返回规范化 content)。确定性回归 2 例(围栏直过、坏答案→围栏修复一次);stop semantics 55 passed、S12 与基线自测全绿。首次基线(engine invalid_output)与修复后(engine normal 全绿)的对比即修复证据。

### 全量验证

- 定向:基线自测 3 passed;stop semantics + S12 + 基线报告 55 passed;runtime 全目录与受影响入口回归在围栏修复后复跑通过(记录于会话日志)。
- `ruff check`/`format --check`:通过;`mypy`:通过(749 files 口径);`openspec validate --strict`:通过。

## 边界与未验证项(如实记录)

- 单模型单层:基线记录为 `zai/glm-4.5-flash` 免费层;4.7-flash 免费层饱和(限流)仅冒烟;OpenAI/Anthropic 无可用凭据。多 provider 对比不在本轮。
- 延迟阈值结论:免费层多轮超 60s 为实测;不据此对付费层下结论。
- 审批续推的 direct 路径为同步回调语义;持久等待归宿主侧后续 change。
- 跨请求续推的上下文恢复缺口与 Runner 选工具、独立 workflow 身份、放量审计均未关闭;默认引擎开关维持关闭;记录不构成生产认证。
