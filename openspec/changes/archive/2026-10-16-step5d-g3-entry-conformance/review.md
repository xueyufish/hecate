# G3 实际入口实现复核

## 范围与基线

基线 `22dbd504`，用户指定 `feat/step5d-g3-entry-conformance` 工作树，GLM 实现未提交。对照 proposal、design、delta spec、tasks 与演进方案，复核真实入口、恢复回执、快照合并及 PostgreSQL 事实。本次修正在同一 change 内完成，不翻转默认引擎开关。

## 发现与修正

| 优先级 | 原实现问题 | 修正及证据 |
|---|---|---|
| P1 | 快照在锁外读取，等待锁期间的新 channel_state 可被旧副本覆盖；读失败仍保存空状态；仅复制部分字段，丢失 intent 和原 replay anchor | 锁内读取并通过 model_copy 合并 Agent 状态，读故障跳过保存。保留 channel_state、intent、metadata 和对应 event_position；Agent 更新不消费尚未折叠的日志尾部。并发更新及读失败两个反例在修正前失败，修正后通过；另有 replay anchor 回归 |
| P1 | 日志回填提取 content 丢弃 is_error，旧版本持久化的对账错误标记会伪装成真实成功结果，绕过本轮停止语义 | 排除错误工具消息。新增“成功回执 + 旧 CHANNEL_WRITE 占位”反例，修正前未抛停止，修正后以 reconciliation_required 停止且无二次工具调用 |
| P1 | MCP 任务已勾选但仅调用私有 _chat_via_entry，未覆盖注册工具、协议取消或拒绝；Workflow 仅一轮工具且最终答案为常量，事件扫描依赖内存私有字段 | MCP 使用 FastMCP Client 初始化协议会话，调用注册 session_create/agent_chat，验证多轮配对、真实回执续接、策略/审批拒绝及协议请求取消后的恢复。Workflow 改为多轮成功和两类拒绝参数化，通过真实 task-control/部署身份子任务，最终答案使用实际回执；新 PG 会话读取 Run、工具配对、拒绝及审批事实 |
| P1 | TCP 只允许 cancelled/failed 之一，没有持久终态信封断言；恢复答案固定为 5，无法证明实际回填；PG 故障注入改用 InMemoryEventStore，绕过持久后端 | 精确断言 cancelled、唯一终态信封和投影停止类别，模型输入逐条核对真实回执。PG ORM、事件与快照均持久化，内容丢失直接在隔离 schema 删除消息通道内容和快照，保留成功回执，并核对恢复停止响应、信封和投影 |
| P2 | TCP 夹具全局 registration/API key/依赖覆盖未完整恢复，池连接由测试循环创建后供服务器线程复用；PG 快照模型未在建表前加载 | monkeypatch 恢复全局装配，服务器连接 NullPool，读侧独立池；建表不预热服务器连接，提前注册快照表。关闭 gate/socket/server，校验线程退出。PG 新增快照认证首轮暴露跨 loop 和缺表错误，修正后专项通过 |
| P1 | verification 声称 PG CI 已包含 Workflow 模块，实际 job 只有旧续跑和入口契约模块；MCP/Workflow 缺项仍标 complete | 现有 PG job 显式增加 TCP、MCP 和 Workflow 模块，拒绝和取消缺项在复核中补齐；更新 G3 决策记录与方案，保留默认关闭和真实支持边界 |

停止类别复用 `ExecutionStoppedError.reason_category` 和入口现有通用映射，不另建停止词汇表或生命周期。

## 验证

- 原 GLM 定向基线：33 passed。
- 快照、Workflow、G2、MCP 与真实 TCP 定向回归：102 passed、2 skipped。跳过项为需要 PG 的 MCP 拒绝审计与独立终态组合，已在 PG 专项实际执行；SQLite 不授予该并发写支持。
- 最终 PostgreSQL 专项：27 passed，222.01s。包括真实 TCP、MCP 协议、Workflow 子任务，以及原生续跑和上一轮入口契约兼容性；并非仅配置后收集测试。TCP 内容丢失仍读写 PG，未用内存替身冒充。
- Runner 共享恢复决策兼容性：test_engine / test_durable 共 35 passed，30.43s。
- Ruff check、源文件/测试格式检查通过；mypy 覆盖 src、hecate-runtime、hecate-durable，749 个源文件零错误；OpenSpec strict validate 与 git diff --check 通过。
- 宽回归：2446 passed、46 skipped、1 failed、13 warnings，613.68s。唯一失败是未改动的 `test_shell_hook_json_stdin` 使用外部 `cat`，Windows cmd 的 PATH 未包含该可执行文件；直接探针返回“cat is not recognized”。同一测试在未改动的 main 基线也失败，故有独立复现依据。仅在复跑进程 PATH 加入现有 Git usr/bin 后，shell hook 整文件 7 passed，12.37s；未改动该产品功能或无关测试。未重复整个宽回归，不将首次失败抹成零失败报告；原失败用例已有通过证据。
- 宽回归跳过包括 JS pilot 依赖未安装、未配置 PG、专用 binding 已另测的多 schema 样例、空参数层次探针，以及可选并发存储用例。其中本轮 PG 入口、Workflow/MCP 拒绝和原生续跑已在上述 PG 专项实际执行；其他跳过项不因此获认证。依赖弃用、已有 AsyncMock coroutine 清理和 fixture 收集警告保留，未静默过滤。

PG 命令使用 `HECATE_STEP6_POSTGRES_URL`，指向本次新建的 `hecate-g3-review-pg` postgres:16 容器、`127.0.0.1:55434` 和一次性测试数据库，各测试独立 schema。验收通过后已核对专属标签并移除该临时容器与测试数据卷；机器上既有业务数据库未使用。

```powershell
.venv/Scripts/python.exe -m pytest tests/test_channel/test_g3_real_tcp_disconnect.py tests/test_mcp/test_agent_chat_entry_parity.py tests/test_execution/test_workflow_child_adapter.py tests/test_execution/test_builtin_continuation.py tests/test_execution/test_entry_contract_pg_e2e.py -q -rs --tb=short --timeout=150 -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/test_channel/ tests/test_execution/ tests/test_runtime/ tests/test_a2a/ tests/test_services/test_scheduling/ tests/test_services/test_workflow/ tests/test_mcp/ tests/test_core/test_im_providers.py tests/test_gateway/test_im_routing.py tests/test_layering_domain.py tests/test_layering_entry_imports.py tests/scenarios/test_s12_autonomous_multiturn.py packages/hecate-runtime/tests/ packages/hecate-durable/tests/ -q -rs --tb=short --timeout=100 -p no:cacheprovider
# Windows shell-hook environment remedy, scoped to this process:
$env:PATH = 'C:/Program Files/Git/usr/bin;' + $env:PATH
.venv/Scripts/python.exe -m pytest tests/test_runtime/test_hooks/test_shell_hook.py -q --tb=short --timeout=60 -p no:cacheprovider
```

## 支持边界

- 本地网络证据为 Windows 上真实 TCP RST 中途断连；不据此认证普通 FIN、代理超时或 Linux 远程 CI。CI 已增加执行命令，远程结果须推送后确认。
- MCP 使用进程内的实际协议会话，未替换执行服务；该证据不等于 MCP 网络传输/鉴权或安装升级认证。
- Workflow 限定为已有部署身份的 Agent 子任务多轮/拒绝及核实回调的父任务收敛；不解决独立 workflow 的 Run 身份，也不认证带工具子任务的中途断连或完整跨进程故障矩阵。
- SQLite 层只持久化 ORM；事件和快照使用内存。PG 层覆盖 ORM、事件和快照；未配置时的 skip 不算 PG 验收通过。
- 默认开关维持关闭；实际放量审计、翻转决策及待发布 profile 的剩余入口支持核对未完成。真实模型质量验收归 step5e，方案 G3 项保持未勾选。
- 修正留在用户指定工作树；本次不提交、推送或归档。
