# Tasks

## 1. 服务层查询

- [x] 1.1 `src/hecate/execution/task_control.py` 新增 `list_run_actions(workspace_id, run_id)`：`get_run` 与父 Task 归属校验（统一 404）→ `run_row_ref` → `asyncio.to_thread(self._store.list_run_actions, ref)`；能力探测可调用查询方法决定 `supported`；白名单逐字段构造（D2 列表，无 `**row` 展开）。验证：`grep` 确认响应构造无 `result_payload`/`session_id`/`claim_token`/`active_claim`。

## 2. 路由

- [x] 2.1 `src/hecate/channel/api/tasks.py` 新增 `GET /runs/{run_id}/actions` 薄路由（照 `get_run` 先例：`ServiceDep`/`AuthDep`/`_workspace_id`/`_map_service_error`）。验证：核对 `_map_service_error` 对存储异常的分支（无则补窄映射）；OpenAPI schema 含新端点。

## 3. 测试

- [x] 3.1 `tests/test_execution/test_run_action_query.py`（服务层，真实 `SqlDurableStore` SQLite）：多 Run 任务各自只返回自己的动作；跨 workspace/不存在 Run 404；历史 Run 不被最新 Run 替换；状态与台账逐一对应（成功/领取/失败/未知各至少一例，不由 Task 终态推断）；白名单（响应键集合恰等于白名单，文本不含 payload 内容/session/claim_token）；stub store 的 `supported: false` 标注；查询零副作用（前后台账行内容、任务状态、命令回执比对一致）。验证：全过并报告用例数。
- [x] 3.2 `tests/test_api/test_run_actions_api.py`（API 层）：200（含 supported/ledger_source）、404 映射、存储故障 5xx 映射、鉴权上下文经真实 HTTP 测试客户端。验证：全过。

## 4. 回归与收尾

- [x] 4.1 显式收集：`python -m pytest tests/test_execution/test_run_action_query.py tests/test_api/test_run_actions_api.py tests/test_execution/test_task_control*.py tests/test_api/test_tasks*.py -q`（按实际文件名定位任务控制相关既有套件一并跑）。验证：全过，报告实际收集数；任何失败须核实原因并记录，跳过不计通过。
- [x] 4.2 共享检查 `bash scripts/dev.sh check all`。验证：0 错误。
- [x] 4.3 汇总 `verification.md`：实际结果、PG 未跑（如未配置则如实标注，不宣称通过）、边界声明（只读切片）。验证：tasks 全勾，验证记录含实际结果；提交、PR 与 CI 留给后续流程。

## 5. 复核修正

- [x] 5.1 查询前同时核实 Run 与所属 Task 的存续和 workspace 归属，错误不泄露存储诊断。
- [x] 5.2 使用共享 ORM fixture 和真实 SQL 台账补齐历史引用、全部状态、HTTP 隔离、故障脱敏及非空事实的零写入证据。
- [x] 5.3 执行受影响回归、共享检查与严格校验；同步规范、设计和真实验证记录。
