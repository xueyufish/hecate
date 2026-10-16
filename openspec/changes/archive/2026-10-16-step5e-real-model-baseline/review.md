# step5e 真实模型基线复核

## 基线与范围

用户指定 `feat/step5e-real-model-baseline` 工作树，基线 `a9364a34`，GLM 实现未提交。对照 proposal、design、spec 与 tasks 检查实际双执行路径、数据采集、输出 Schema、审批和 TCP 恢复。本次只在既有 change 内修正，不提交、推送或归档；默认引擎开关保持关闭。

## 发现与修正

| 优先级 | 原实现问题 | 修正与证据 |
|---|---|---|
| P1 | `_enforce_output_schema` 在没有 Schema 时仍剥离普通代码围栏；引擎 HTTP 先校验，围栏被认为有效后直接返回原文，客户端收到不可直接 JSON 解析的内容 | 无 Schema 原文返回，声明 Schema 的 direct/engine 与同步/流式均规范化最终结果；格式修复仍禁用工具。补 helper、direct loop 和实际 HTTP/SSE 用例 |
| P1 | 采集器只记录 tool_calls 与延迟，不保存模型输入/输出、逐轮 usage、工具回执及审批事件；普通 provider 异常在外层被替换成空 rounds 和未知副作用，掩盖已完成事实 | v4 逐轮保存实际输入/输出/usage 和错误类型，工具回执与审批决定、ASKED/DECIDED 独立记录；run_case 捕获后保留异常前事实。注入 provider 在成功读取后失败的反例验证 rounds/receipt 保留 |
| P1 | 只有 BASELINE_MODEL 名称也启动真实采集；空 usage 被 all([]) 判为已测零 token；审批合规仅凭询问次数/Schema，产物标识只做全文子串匹配；阈值在 Schema 中未固定 | 根据选中 provider 检查凭据，空 usage 为未知；审批需要先拒后批的事件与决定、拒批时无副作用、逐字参数和正确产物。阈值 const 固定，错误 artifact suffix 不能得分；独立反例覆盖 |
| P1 | 原真实断连测试使用 InMemorySessionStateStore，却声称 PG 快照持久；既未查询唯一终态信封，也未断言恢复首轮回执或正确答案，仅打印上下文缺口后算 passed | 复用 G3 同一 fixture，PG ORM/事件/快照均持久，新存储对象恢复、新 reader 查询；精确 cancelled 信封/投影和单次成功回执，恢复首轮必须含真实回执，最终 JSON 引用实际计算结果。真实/确定性观测分开，真实失败也写独立记录 |
| P1 | 新请求仅给续接 user 消息时，WorkflowExecutionService 从空消息通道起步；此前 G3 稳定 call id 会重新提工具再回填，不能证明无需重提工具的上下文恢复 | 新确定性 TCP 反例在原实现恢复首轮工具消息为空，明确失败。服务端记录同用户同 Agent 归属后，续接折叠日志并合并新消息；完整历史去重。其他用户、不同 Agent、无可信标记的旧快照均不授权读取，新增隔离测试；不回填旧快照归属 |
| P2 | 原真实模型夹具未完整恢复全局 registration/依赖/API key，失败时可能遗留线程和 schema；文档把上下文机制缺口直接延期，任务却全部勾选 | 改为复用已验证的 G3 夹具，不复制易漂移的服务器实现；补精确断言和实际记录，再按证据更新状态，不把未达标观测称作认证 |
| P1 | 新 v4 实测显示 engine 已创建正确工单，但格式修复丢掉实际工具回执，最终 JSON 改称缺少材料；仅靠原模型自然语言摘要不能保证修复后的事实正确 | 注入 provider 反例 direct 通过、engine 失败。HTTP 引擎和采集器都改为保留最后实际模型上下文进行一次禁用工具的格式修复。反例通过，原实测失败文件保留，只有受影响 engine normal/approval_resume 另行补采 |

## 记录与版本原则

- 原 GLM rubric v3 文件保持复核前内容，不重算旧分数。其缺少的原始内容、回执和审批数据不能事后补造。
- v4 评分和原阈值在实际采集前固定；新记录使用独立时间戳文件，区分 `source=real_model` 与注入的 `synthetic`。provider 自动重试关闭，顺序采集，失败不重跑刷分。
- 同步先拒后批只验证回调续推，持久审批等待仍明确未验证。成本与供应商价格未核实，保持 null/unverified；gate=false，不构成默认切换或生产认证。

## 验证进度

- Schema/停止/S12/基线采集定向：64 passed。
- 上下文恢复、隔离、快照与采集器定向：18 passed，1 条缺真实模型凭据的条件 skip。
- PostgreSQL 定向：15 passed，133.25s，含新确定性 TCP 上下文恢复、G3 断连、原生续跑与前轮入口契约。使用独立 `hecate-step5e-review-pg` postgres:16，端口 `127.0.0.1:55435`，各测试独立 schema，未接入机器上既有业务数据库。
- 全部验收结束后已验证专属容器标签并删除临时容器与测试数据卷，既有业务容器保持原状。文档链接核查同时修正方案中一处既有 managed-lease 归档链接。
- Ruff 与格式检查通过；mypy 覆盖 src/runtime/durable，749 个源文件零错误；strict OpenSpec validate 通过。
- 宽回归：2423 passed、47 skipped、13 warnings、零失败，568.64s。Windows 仅给测试进程 PATH 补入已安装 Git usr/bin，避免仓库既有 shell-hook 对 cat 的依赖。范围覆盖 channel、execution、runtime、A2A、scheduling、workflow、MCP、IM、S12、基线与 runtime/durable 包。
- 后续格式修复上下文反例：首次 direct 通过、engine 失败；修正后的定向组 78 passed、1 skipped。完整 channel/场景复核 132 passed、1 skipped；完整 workflow 服务/场景复核 115 passed、1 skipped。最终归属与显式历史处理调整后的 PG/snapshot/TCP 组 13 passed，35.42s。不同运行有重叠，不相加为唯一用例数。
- 跳过项包括缺 JS pilot 依赖、未配置的 PG 专项、专门 binding 已覆盖的多 schema 样例、空参数探针和手动真实模型场景。本轮涉及的 PG 原生续跑/入口契约与真实模型另有实际执行证据；其余跳过不授予认证。既有 fixture 收集、第三方弃用和 coroutine 清理警告未静默过滤。

## 实际模型记录

模型为 `zai/glm-4.5-flash`，API key 从现有主 checkout 配置读取，未输出或写入报告。只调用合成工具，无真实业务写入。模型具体版本、价格与最终账单未核实。

- [首次完整 v4](../../../tests/scenarios/baselines/autonomous_multiturn_autonomous-execution-rubric-4_20261016T083041343667Z.json)：双路径 × normal/approval_resume/needs_review/budget_exhausted/cancelled，全部实际运行且无 collection_error。停止类别和副作用计数符合预期；direct normal/approval 达到工具与产物要求但延迟分别约 60.2s/74.9s，超过预注册阈值。engine normal/approval 的工具已正确执行，但格式修复后丢失产物事实，被严格评分明确判为失败；没有把这两项改成模型质量豁免。
- [机制修复后的限定补采](../../../tests/scenarios/baselines/autonomous_multiturn_rubric-4_repair_context_20261016T084148839574Z.json)：只采 engine normal/approval_resume，关联首次 parent_record，不覆盖前一文件。工具选择、逐字参数、产物、零重复与审批事件要求通过；normal 延迟约 73.0s 仍超阈，approval 约 59.2s 在阈值内。该文件不是重跑整个双路径比较，也不证明总体通过率或性能优势。
- [真实 PG TCP 恢复](../../../tests/scenarios/baselines/real_model_disconnect_v1_20261016T083351993460Z.json)：1 passed，143.04s；实际 provider rounds=3。断连终态和单次回执从 PG 新会话读取，重建存储对象后，恢复首轮含真实内容 `5`，最终结构化结果正确，无第二次工具执行。
- [真实 SQLite TCP 恢复](../../../tests/scenarios/baselines/real_model_disconnect_v1_20261016T083647368984Z.json)：1 passed，86.44s；实际 provider rounds=3。仅为 SQLite ORM + 内存事件/快照的进程内恢复，不冒充 PG/进程重启证据。
- 全部新记录 cost_amount=null、price_version=unverified、gate=false。原 GLM v3 文件未被复核修改；v4 的首次失败与后续补采均保留。

复现真实恢复需设置 `HECATE_RUN_REAL_MODEL_BASELINE=1`、`BASELINE_MODEL` 和所选 provider 的 key；PG 层另设 `HECATE_STEP6_POSTGRES_URL`。后续增加的显式 opt-in 仅控制采集选择，不改变已记录的恢复行为。真实模型不属于默认 CI 质量断言。

## 支持边界

上下文恢复仅对新且空的 namespace 或服务端既有可信归属生效，不推断/认领旧日志。显式客户端完整历史保持原语义，权限是单次执行事实，不共享可变 service 标志。PG 新存储对象恢复不等于操作系统进程重启矩阵；SQLite 只认证进程内事件/快照恢复。MCP/Workflow、部署/Run 身份、持久审批、Runner 自主选择、其他 provider、实际放量和业务收益按原方案另行验收，不因本轮新增质量记录关闭。
