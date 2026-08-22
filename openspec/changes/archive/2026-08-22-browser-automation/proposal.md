# Proposal: Browser Automation Tool (6.27)

## Why

Hecate 当前的内置工具集(`web_search` / `read_file` / `write_file` / `list_files` / `execute_code`)只能让 Agent 与静态内容(本地文件、搜索引擎结果、一次性 Python 代码)交互,**无法访问现代 Web 应用**。真实的 Agent 用例(登录后抓取、提交表单、点击按钮、滚动 SPA、读取动态渲染的页面、登录后的 dashboard)全部需要真实的浏览器。

同时,P3 阶段已经把 `5.1` Builtin Tools、`9.4c` Docker Sandbox Executor、`9.12` Network Egress Control、`5.14` Environment Security、`9.4` 内容感知门控、`5.6` Tool Permission Control 等基础设施全部交付完成 —— **底层就绪,缺的是工具本身**。这正是 P3 最后两块拼图中的一块(`5.4b` 是另一块),落地后 P3 即可 close-out。

> 参考对标:微软 Playwright MCP(36k★,使用 a11y tree 作为 LLM-facing 表征,业界事实标准)、Anthropic Claude Code computer-use(浏览器变体)、Browser-Use、Skyvern、Manus Browser Operator。

## What Changes

新增 6 个 LLM-facing 内置浏览器工具,以及支撑它们运行的浏览器会话管理子系统:

- **6 个新 builtin tools**(通过 `BUILTIN_TOOL_DEFINITIONS` 注册,自动接入 `ToolRegistry` 路由):
  - `browser_navigate(url, wait_until?)` —— 导航到指定 URL
  - `browser_click(selector, text?, index?)` —— 点击页面元素(text-based 优先,index 兜底)
  - `browser_type(selector, text, submit?)` —— 在输入框输入文本
  - `browser_extract(selector?, mode?)` —— 提取页面内容(mode: `text` / `html` / `a11y`)
  - `browser_screenshot(full_page?, selector?)` —— 截取页面截图
  - `browser_fill_form(fields: [{selector, value}])` —— 批量填写表单字段

- **1 个隐式能力**:每个 agent session 自动获得一个独立的浏览器会话(per-session 持久生命周期),session 结束自动回收。

- **1 个新的沙箱镜像**:`docker/sandbox/Dockerfile`,基于 `python:3.12-slim` 构建,内含 Playwright Python SDK + Chromium 浏览器运行时 + tool dispatcher 入口。

- **1 个新的网络策略门**:`NetworkPolicy` 扩展 browser-specific 规则;默认 fail-closed(空白名单 → 拒绝所有出站)。

- **风险等级门控**(继承自 9.4):6 个工具默认 `MEDIUM`;导航到非白名单域名时升级为 `HIGH`,触发 `ApprovalCallback`。
- **DLP 扫描**(继承自 9.10):`browser_extract` 的返回文本与 `browser_screenshot` 的截图都进入现有 DLP 管道。

**无破坏性变更**:所有现有 builtin tool 行为保持不变;6.27 是纯增量。`BUILTIN_TOOL_DEFINITIONS` 字典是 Open-ended 添加,旧客户端(如已保存的工具快照)继续工作。

## Capabilities

### New Capabilities

(无。本次变更不引入新的 capability 目录,所有 LLM-facing 行为都属于 `builtin-tools` 已有能力域。)

### Modified Capabilities

- `builtin-tools`: 在 `spec.md` 中新增 7 个 `ADDED Requirements`(6 个工具 + 1 个浏览器会话生命周期),覆盖 LLM-facing JSON Schema、行为约束、失败模式、以及与 sandbox / network policy / DLP 的集成契约。

## Impact

### 新增/修改的文件

| 文件 | 性质 | 改动概要 |
|---|---|---|
| `src/hecate/services/tool/builtin.py` | 修改 | 扩展 `BUILTIN_TOOL_DEFINITIONS`(6 个新 schema),扩展 `BuiltInToolExecutor.execute()` 路由 |
| `src/hecate/services/browser/` | 新建 | 新模块,容纳 `BrowserSessionManager`(per-session 浏览器生命周期管理) |
| `src/hecate/services/browser/session.py` | 新建 | Playwright async API 封装,CDP 连接、selector 解析、a11y tree 序列化 |
| `src/hecate/services/browser/__init__.py` | 新建 | 模块导出 |
| `src/hecate/services/environment/network_policy.py` | 修改 | 扩展 browser-specific 规则(域名白名单应用到 outbound HTTP 请求) |
| `pyproject.toml` | 修改 | `[tools]` 组新增 `playwright>=1.40` |
| `docker/sandbox/Dockerfile` | 新建 | Chromium + Playwright + tool dispatcher 入口 |
| `docker/sandbox/entrypoint.py` | 新建 | 容器内 tool 分发器:读 `TOOL_INPUT` env,执行对应 tool handler |
| `docker/docker-compose.yml` | 修改 | 添加 sandbox image build step(可选) |
| `openspec/specs/builtin-tools/spec.md` | 修改 | 7 个 ADDED Requirements |
| `tests/test_services/test_browser/` | 新建 | 浏览器工具单元测试 + 集成测试 |
| `tests/test_services/test_browser/test_session.py` | 新建 | BrowserSessionManager 单元测试 |
| `tests/test_services/test_browser/test_builtin_tools.py` | 新建 | 6 个 tool 的 executor 单元测试 |
| `tests/test_services/test_browser/test_integration.py` | 新建 | 真实 Playwright + Chromium 的集成测试(默认 skip,如未安装浏览器) |
| `docs/how-to/browser-automation.md` | 新建 | 用户文档:如何启用、风险等级、网络白名单、headless/headful 选择 |

### 新增依赖

- `playwright>=1.40` —— 浏览器自动化 SDK(放在 `[tools]` 组,与 `aiodocker` 同组)
- 镜像内系统依赖:`libnss3`、`libatk1.0-0`、`libxkbcommon0`、`libgbm1`、`libasound2t64`、`chromium`(由 `playwright install` 拉取)

### 依赖的已完成特性

- `5.1` Builtin Tools ✅ —— JSON Schema 注册 + `BuiltInToolExecutor` 路由
- `9.4c` Docker Sandbox Executor ✅ —— 沙箱容器管理基础
- `9.4d` Sandbox Container Pool ✅ —— 池化容器 + `docker exec` 长驻进程支持
- `9.12` Network Egress Control ✅ —— per-environment 域名白名单
- `5.6` Tool Permission Control ✅ —— 风险等级门控 + ApprovalCallback
- `9.10` Outbound DLP Engine ✅ —— 输出扫描(文本 + 截图)
- `9.4` 内容感知门控 ✅ —— PreToolHook/PostToolHook 通用机制
- `9.14` Structured Audit Pipeline ✅ —— ToolDecisionModel 自动记录

### 推迟到 P4/P5 的事项

- `browser_evaluate` —— 让 agent 跑 JS(自定义提取);等真有用例再加
- `browser_console_messages` / `browser_press_key` / `browser_select_option` / `browser_tabs` 等 12+ 工具 —— 与微软 Playwright MCP 完整对齐,但 v1 不必要
- Headful 模式(noVNC viewer) —— 触发条件 = 第一个明确要可视化调试的客户
- Cloud browser provider(Browserbase / Steel.dev) —— 本地沙箱足够,先不上
- 持久化 user-data-dir(跨 session 持久 cookie) —— 安全风险,不做
- `6.27a` Computer-use(OS 级别 GUI 自动化) —— 拆出去留在 P4
