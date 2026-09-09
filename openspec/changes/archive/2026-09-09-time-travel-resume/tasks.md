# Tasks: 1.3.21② Time-Travel Resume + update_state

## 1. 事件层(D1/D3)

- [x] 1.1 `runtime/eventstore.py`:`EventType` 新增 `FORK = "FORK"`;单测:枚举字符串、append/storeable 往返、未知类型回落 CUSTOM 既有行为不回归
- [x] 1.2 `runtime/replay/logfold.py`:`fold_session` 增加 `FORK` 分支——payload `channel_state` 经 `channel_manager.restore` 整体水合(剔除载荷中意外出现的 `_`/`sys.` 前缀通道,防御性),其后 CHANNEL_WRITE 增量应用;`FORK`/`INTERRUPT` 对 fold 中立;单测:`[FORK(state), CHANNEL_WRITE(x,1), STEP_END]` 折叠 = 快照叠加 x=1、水合为替换非合并、projection 等价
- [x] 1.3 撕裂尾部规则对齐:`FORK` 纳入提交点类别(恢复/inspect 的提交点集合常量与判定);单测:子会话撕裂尾部回退到 FORK 版本
- [x] 1.4 LogInvariants 回归:构造含 FORK 与 update_state 批次的事件流跑 `run_all`,确认无违例;若存在要求 CHANNEL_WRITE turn 闭包的既有不变式,以 D4 的 TURN 对包裹形状为准补测试

## 2. 续跑推导器(D5/D6)

- [x] 2.1 新模块 `runtime/replay/continuation.py`:输入事件流(至目标版本)+ CompiledGraph,输出续跑节点集——未闭合 INTERRUPT 复用 ① phase-aware 规则(抽取共享,避免与 `_load_interrupt_descriptor` 两份实现)、FORK 取 payload `next_nodes`、STEP_END 取自上一提交点以来 NODE_END 集出边并集(条件边读恢复态 `_route`,复用 `_resolve_conditional_target` 语义)、无提交点取 entry;单测矩阵覆盖四类锚点 + 混存优先级(日志顺序取最后)
- [x] 2.2 `runtime/pregel.py` 恢复路径统一化:`_restore_from_checkpoint` 扩展接受目标版本——尾部 = 现行为逐字保持;`resume_from` 守卫(V < 尾部抛错并指引 fork,V == 尾部折叠至 V);续跑节点集改由 2.1 推导器供给(INTERRUPT 路径行为不变,回归 ① 测试全绿)
- [x] 2.3 单测:尾部崩溃恢复(STEP_END 锚点)续跑 = NODE_END 出边并集;`resume_from < tail` 抛错且日志零追加

## 3. update_state 服务(D4)

- [x] 3.1 studio/workflows 新增状态修改服务:门控(未闭合 TURN 且其后无 ERROR → 409;LogPolicy 排除通道 → 422)、批次落盘(TURN_START(reason="update_state") → CHANNEL_WRITE×N(source/actor) → STEP_END(source="update_state", superstep 取日志尾推导值) → TURN_END)、经 `ChannelManager.write` 应用 reducer 语义、响应返回折叠后全量状态 + log_version
- [x] 3.2 单测:中断态改值 → resume 后值可见且续跑推导不变;messages 追加非替换;执行中 409;ERROR 逃逸放行;审计形状(每 channel 恰一条 CHANNEL_WRITE,payload 含 source/actor);落盘后投影等价通过

## 4. fork 编排(D2/D3/D7)

- [x] 4.1 studio/workflows 新增 fork 服务:校验(at_version ≤ 尾部否则 422、锚点向下对齐返回 effective_version)、折叠父前缀至锚点(可从 ≤ 锚点的 checkpoint 缓存加速,缓存缺失走全量 fold)、经 2.1 推导 next_nodes、建子会话行(metadata 记 parent_session_id/parent_log_version/agent_id)、落 FORK 事件(载荷按 D3,剔除临时通道)、可选 updates 走 3.1 同一批次路径、以 `resume_from=子日志尾部` 拉起执行(复用 2.2 恢复路径读 FORK 描述符)
- [x] 4.2 单测:从中断锚点 fork 带 updates → 子会话立即续跑且 update 先落盘;非提交点向下对齐;父日志逐字节不变;同锚点多次 fork 互不影响;子会话崩溃后仅凭自身日志恢复 + 投影等价通过;旧枚举读端把 FORK 读为 CUSTOM 时 fold 中立

## 5. API 层(D2/D7/D8)

- [x] 5.1 `studio/api/sessions.py` 新增三端点:`GET /sessions/{id}/commit-points`(日志派生,limit 默认 20 降序,复用 replay 提交点机制)、`POST /sessions/{id}/fork`(at_version/updates,响应含子会话数据 + lineage + effective_version + 副作用重执行声明字段)、`POST /sessions/{id}/state`(values,响应含折叠态 + log_version);全部 workspace 隔离 + 404 语义对齐既有端点,OpenAPI description 含副作用声明
- [x] 5.2 `GET /sessions` 增加 `parent_session_id` 过滤;`SessionReadSchema` 暴露 parent_session_id/parent_log_version(非 fork 会话为空)
- [x] 5.3 API 集成测试(扩展现有 test_api 会话用例):三端点正反路径(200/201/404/409/422)、lineage 过滤恰好命中同源分支

## 6. 文档与验证

- [x] 6.1 文档修正:`docs/concepts/sessions.md` 将不存在的 checkpoints curl 示例替换为 `GET /commit-points` 真实契约;`docs/how-to/replay-debug-guide.md` 增补"从提交点 fork 重跑"用法与副作用重执行警告;遵守 writing-style(无数值日期标记)
- [x] 6.2 全量验证四项检查全绿:`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/ -q`;确认 ① 既有中断/resume 测试零回归
