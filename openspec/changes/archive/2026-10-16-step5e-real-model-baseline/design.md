# Design

## Context

代码检视确认的实现面:

- **采集器**(`tests/scenarios/baseline/run_autonomous_baseline.py`,373 行):已实现 direct(`_chat_with_tools`)与 engine(`EntryExecutionService`)双路径 × 4 案例(normal/needs_review/budget_exhausted/cancelled),rubric v2 评分(工具选择首调 `lookup_material(domain_a, SKU-A1)`、`ticket_body` 逐字、schema 校验、停止类别×副作用组合),`RecordedModelService` 逐轮记录 tool_calls/latency/usage,成本恒为 null+unverified;`OPENAI_API_KEY` 缺失时显式 skipped。报告 schema `_meta.gate: const False`(记录即非门禁)。
- **既有基线**:`tests/scenarios/baselines/autonomous_multiturn_baseline.json` 为 skipped 记录;ADR-036(63 行)与机制矩阵(31 行)明确等待真实记录定稿,并把"durable approval/resume with a real model"列为未验证。
- **G3 harness**(`tests/test_channel/test_g3_real_tcp_disconnect.py`):in-thread uvicorn 真实 socket + RST 断连 + 恢复,已验证确定性;本轮把真实 provider 接入同一模式。
- **主规格**:`autonomous-execution-parity`(5 条确定性要求)。

## Goals / Non-Goals

**Goals**

1. 双路径 × 全案例的 recorded 真模型报告(替代 skipped)。
2. 新增两个真模型场景:审批续推、真实断连恢复。
3. 机制矩阵与 ADR-036 实测定稿;实测暴露缺陷的针对性修正。
4. 确定性回归零破坏(采集器自测用注入 completion 双打)。

**Non-Goals**

- 独立 Runner 自主选工具、宿主侧持久审批续推、独立 workflow 部署/Run 身份(各自后续 change)。
- 默认开关翻转、发布认证、放量审计。
- 多 provider 矩阵:主模型单一(可复现),其余 provider 记录未测。
- 业务价值/真实企业负载验收。

## Decisions

### D1: 采集器扩展而非重写

沿用 `run_autonomous_baseline.py` 的骨架与 rubric 版本化机制,做三处扩展:

1. **新增案例 `approval_resume`**:`StubBusinessRegistry` 增加审批语义——`create_test_ticket` 首次调用经 `approval_callback` 询问,采集器侧确定性权威先拒一次再批准(覆盖"审批被拒工具不执行"与"批准后继续"两条),批准后模型继续推理至结构化产物。评分项:`approval_resume_compliance`(批准路径产物合规、拒批零副作用、审批事件对留痕)。direct 路径的同步审批回调与 engine 路径的 ToolWorker 审批回调都走采集器的同一权威。
2. **rubric v3**:阈值沿用(60s/20k/$1),`_score` 增加 `feedback_params_exact`(ticket_body 逐字,从既有 `derived` 提升为独立项);报告 `_meta.unverified` 更新(移除"real-model approval/resume",保留 Runner/业务价值/放量)。
3. **成本三态**:`usage.source` 保持 reported;`cost_amount` null + `price_version: unverified`;新增 `cost_threshold_conclusion: "unverified"`(显式未知,不以 token 估算填 `$`)。

### D2: 断连恢复场景独立模块(真 provider × G3 harness)

新增 `tests/scenarios/baseline/run_real_model_disconnect.py`(手动运行,同采集器输出约定):复用 G3 的 in-thread uvicorn + 原始 socket RST 模式与三引擎隔离,但 provider 为真实模型(`OPENAI_API_KEY`),工具为合成注册表。时序:真实模型提出首个工具调用并执行成功→答案轮 park(确定性 gate)→RST 断连→新请求同会话恢复→断言:恢复请求中工具零重复(回执真实回填——G3 已交付的日志回填机制在真模型轮次上成立)、最终结构化产物合规、事件/终态从持久存储新会话可查。SQLite 层运行;PG 参数化沿用。该模块证明的是"真模型轮次的恢复保护",不重复 G3 的机制断言。

### D3: 预注册先于运行

阈值与评分项在 tasks 完成前固化进采集器(rubric v3 常量),报告 schema 校验 `_meta.thresholds` 与实际一致;真模型运行只是采集,不允许运行后改阈值重跑刷分——若需调阈值,新 rubric 版本号并保留旧记录。

### D4: 实测缺陷的处理边界

预期风险点:真模型不按提示词先读材料、修复轮破坏工具禁用、审批等待在 engine 路径的 interrupt 语义与同步回调不一致。实测暴露的确定性缺陷(机制级)→ 本 change 内修正并附确定性回归;模型行为偏差(质量级)→ 记录进报告与矩阵,不做 prompt 调参刷分(调参=新 rubric 版本+新记录)。区分标准:复现于注入 completion 双打的确定性回归即机制级。

### D5: 文档定稿

- 机制矩阵:矩阵表增加"Real-model record"行(或证据节引用 recorded 报告与两个新场景),证据节的 skipped 说明改为 recorded 引用 + 剩余门禁更新。
- ADR-036:Status 从"pending"改为"real-model baseline recorded (gate closed: no)";Decision 增补实测结论(双路径质量/延迟/token 对比、发现的缺陷与修正);不改变"不授权默认切换"的结论。
- 方案 step5e 勾选项:本轮覆盖"真实模型执行多轮任务+预算/取消+审批后继续推理+重启恢复+未知写结果停止"的**记录级**验收;生产启用仍依赖 step6/7、最小 step10/11 与 step16(勾选项按方案原文评估,不扩大声明)。

## Risks / Trade-offs

- **真模型不稳定**(温度 0 仍有采样差异):rubric 评分项设计为行为断言(工具序列/参数/schema)而非文本相似;若单轮失败,记录失败本身(不重试刷分);阈值结论按实测写。
- **成本无法闭环**:保持未知(方案 G4 明确未知不记零、不承诺硬限制);报告显式三态。
- **审批续推在 direct 路径的局限**:direct 的同步审批回调不等价持久等待——本轮 direct 路径只验"询问-批准-继续",持久等待的权威归后续 change(与未验证清单一致,不冒充)。
- **运行成本**:2 路径 × 5 案例 + 断连场景 ≈ 12 次真实任务,单任务预算 20k token 上限,总额可控。

## Migration Plan

纯验收与测试基建扩展 + 可能的缺陷修正;无 schema/契约迁移。基线 JSON 直接替换(skipped→recorded),git 历史保留旧记录。

## Open Questions

无——四组范围、rubric 预注册、成本未知三态与"记录≠认证"已在立项时与用户确认。

## 复核修正

- rubric v3 记录保留原样；新增 v4 在运行前固定评分与阈值，使用带时间戳的独立文件，禁止覆盖或改写既有观测。无自动 provider 重试。逐轮保留实际输入、输出、usage 与错误类型，工具回执、审批事件和决定单列；采集中断不丢弃已完成事实。
- 真凭据按实际选定 provider 检查，只有 model 名称不算凭据；空 usage 不报告零 token。审批合规需真实 ASKED/DECIDED 对、先拒后批的决定、副作用发生时序、逐字反馈参数与正确产物；计数本身不构成审批留痕。
- JSON 围栏规范化仅作用于声明 Schema 的最终结果。引擎 HTTP 路径即使首次校验已通过，也须返回规范化内容；格式修复不得重启工具执行。
- 确定性 TCP 反例复现了“续接只给新 user 消息，先前工具回执不进入模型”。新且空的会话 namespace 或已有可信归属才可写服务端同用户同 Agent 标记；续接验证后再折叠持久日志。权限作为执行局部事实传递，不放在可并发复用的 service 状态；有既有日志却无本用户归属的会话不允许被新快照认领。显式客户端历史保持权威，delta 才补充历史并对精确后缀去重。跨 Agent、其他用户和旧快照不授予日志读取，也不回填旧归属。
- 真实模型与确定性恢复使用相同 G3 TCP fixture。PG 层实际使用 PostgresSessionStateStore，重建存储对象后恢复，并从独立 reader 查询事件/终态。回执必须在恢复首轮模型输入中，最终 JSON 要引用正确结果；不能以打印质量缺口替代断言。SQLite 只认证进程内事件/快照恢复，不冒充 PG 持久性。
- 初次 v4 实测还暴露了格式修复只给初始 user 输入、未给工具回执的问题。确定性 provider 可复现，故归入机制修正；HTTP 引擎与基线修复轮均保留实际最后模型上下文，禁用工具。评分与预注册输入不改，保留首次 v4 记录，另写仅含受影响 engine normal/approval_resume 的补采文件并关联 parent_record。
- 真实模型 pytest 需要 `HECATE_RUN_REAL_MODEL_BASELINE=1` 明确启用，CI 默认只跑确定性分支，不因环境偶有 API key 而自动产生模型调用。
