# Tasks

## 1. 引擎路径真实入口证据测试

- [x] 1.1 新增 `direct_path` fixture(显式依赖 autouse `engine_path` 后将 `CHAT_TOOL_LOOP_ENGINE_ENABLED` 置回 False),供关态钉住用例使用;不改动既有 autouse fixture
- [x] 1.2 关态钉住用例:真实 HTTP 入口(流式 + 非流式)在全局开关关闭时走直连循环,响应语义与迁移前一致;断言引擎事件日志不因该请求产生条目(与开态用例形成对照)
- [x] 1.3 引擎路径取消用例:会话流式执行中客户端断开,断言进行中工具轮恰好完成一次、会话快照持久、恢复后不重复执行副作用、取消终止路径被走到(step5e `cancelled` 类);复用既有流式中断与轮询等待模式
- [x] 1.4 跨请求多轮连续性用例:同一 `session_id` 连续两轮请求,断言第 2 轮模型消息含第 1 轮回答、路径亲缘保持、前轮已决回执不重复执行、`validate_tool_pairing` 跨轮完整
- [x] 1.5 确定性拒绝用例:经 `tool_rules` 注入确定性 DENY,断言拒绝记录可查询、对话不终止、响应与直连呈现等价;若暴露不等价为缺陷,最小修复并在复核记录登记

## 2. 默认启用决策记录与文档

- [x] 2.1 新增 `docs/refactor/step5d-g3-default-enable-review.md`:逐入口证据清单(各项指向具体测试;MCP/workflow 入口如实标注缺口)、放量审计核对、决策(维持全局默认关闭 + 翻转前置条件)、旧循环限制显式保留成文;遵守 writing-style(不用数字/日期作标记)
- [x] 2.2 决策记录与 `docs/design/execution-mechanism-matrix.md`、S12 场景归档件交叉引用可解析
- [x] 2.3 `docs/refactor/enterprise-agent-platform-evolution-plan.md` step5d 剩余项行更新:本项完成状态与剩余缺口(MCP/workflow 入口证据)如实登记

## 3. 验证与收尾

- [x] 3.1 定向回归:`test_chat_engine_g3_entry.py` 全量、`tests/test_channel`、S12 场景不回归;新增用例在关态/开态下各自通过
- [x] 3.2 全门槛:`ruff check`、`ruff format --check`、`mypy`、触及面 pytest 全绿
- [x] 3.3 复核记录声明未验证项(MCP/workflow 入口引擎路径证据仍缺、真实模型维度不在本 change 范围),不冒充默认翻转已完成
