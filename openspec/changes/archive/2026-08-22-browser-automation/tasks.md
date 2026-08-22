## 1. 依赖与沙箱镜像

- [x] 1.1 在 `pyproject.toml` 的 `[project.optional-dependencies.tools]` 组添加 `playwright>=1.40,<2.0` —— 期望:`uv pip install -e ".[tools,dev]"` 后 `python -c "import playwright; print(playwright.__version__)"` 输出 1.40+
- [x] 1.2 创建 `docker/sandbox/Dockerfile` —— 基于 `python:3.12-slim`,安装 `playwright==1.40.*` + system deps(`libnss3`、`libatk1.0-0`、`libxkbcommon0`、`libgbm1`、`libasound2t64`),执行 `playwright install --with-deps chromium`,设置 `ENTRYPOINT ["python", "/opt/sandbox/entrypoint.py"]`;期望:`docker build -t hecate-browser-sandbox:latest docker/sandbox` 成功,镜像大小 ≤ 700MB
- [x] 1.3 创建 `docker/sandbox/entrypoint.py` —— 读取 `TOOL_INPUT` env(JSON `{tool, args}`),分发到本地 handler 字典;每个 handler 是 `async def` 函数,接受 `args` dict 返回 dict;启动时 `print("sandbox entrypoint ready", flush=True)` 让上层 `docker wait` 能立即读到;期望:`TOOL_INPUT='{"tool":"browser_navigate","args":{"url":"https://example.com"}}' python entrypoint.py` 在没有真浏览器时报错但能 dispatch
- [x] 1.4 更新 `docker/docker-compose.yml` —— 添加 `hecate-browser-sandbox` service,`build: ./sandbox`,暴露 CDP 端口范围(`9222-9322`)通过 `ports`;期望:`docker compose -f docker/docker-compose.yml build hecate-browser-sandbox` 成功
- [x] 1.5 添加 `docker/sandbox/.dockerignore` 排除 `tests/`、`.venv/`、本地无关文件 —— 期望:context size ≤ 5MB

## 2. 浏览器子系统(`src/hecate/services/browser/`)

- [x] 2.1 创建 `src/hecate/services/browser/__init__.py` —— 导出 `BrowserSession` 和 `BrowserSessionManager`,写模块级 docstring 描述 per-session 长驻生命周期;期望:`from hecate.services.browser import BrowserSession, BrowserSessionManager` 不抛 ImportError
- [x] 2.2 实现 `src/hecate/services/browser/session.py` 的 `BrowserSession` 类 —— 持有 Playwright `Browser` 和 `BrowserContext` 引用,方法:`navigate(url, wait_until)` / `click(selector, text, index)` / `type_text(selector, text, submit)` / `extract(selector, mode)` / `screenshot(full_page, selector)` / `fill_form(fields)`;每个方法返回 dict,异常转成 `{"error": "...", "detail": "..."}` 结构;期望:`pytest tests/test_services/test_browser/test_session.py` 6 个方法都通过(用 mock Playwright)
- [x] 2.3 实现 `BrowserSessionManager` 类(`session.py` 同文件) —— 用 `dict[session_id, BrowserSession]` 维护活跃 session,`get_or_create(session_id)` 懒创建,`close(session_id)` 显式关闭,`close_all()` 在 shutdown 时调用;`create_session` 内部走 `SandboxPool.allocate()` + `DockerEnvironment.exec_shell` 启 chromium + `connect_over_cdp`;期望:单元测试验证 session 复用、关闭、容器退役后自动重建
- [x] 2.4 实现 selector 解析器(`session.py` 内的 `_resolve_click_target` 私有方法) —— 优先级:`text` 非空 → 按可见文本 + role 匹配 → 唯一匹配则用;否则 `selector + index` 按 CSS/XPath + 位置取;匹配 0 个 → `element_not_found`;匹配 >1 个 → `ambiguous_selector` 并返回 count;期望:单元测试覆盖 4 个分支(唯一 text / 唯一 selector / 模糊 selector / 无匹配)
- [x] 2.5 实现 a11y tree 序列化(`session.py` 内的 `_serialize_a11y` 私有方法) —— 调用 `page.accessibility.snapshot()` 递归遍历,生成 `[role] name [state]` 的可读文本;大文档截断到 50KB 并加 `[truncated]` 标记;期望:对一个简单测试页,输出非空且包含 role 信息
- [x] 2.6 创建 `tests/test_services/test_browser/__init__.py` + `conftest.py`(可选) —— 提供 `mock_session_manager` fixture 和 `sample_page` fixture(用 `MagicMock` mock Playwright `Page` 对象);期望:`from tests.test_services.test_browser.conftest import mock_session_manager` 可用

## 3. 网络策略扩展(`src/hecate/services/environment/network_policy.py`)

- [x] 3.1 添加 `apply_to_container(container_id: str, allowed_domains: list[str])` 方法 —— 把 `allowed_domains` 通过 DNS 解析成 IP 段,生成 iptables 规则(`-A OUTPUT -d <ip>/<mask> -j ACCEPT`),最后 `-A OUTPUT -j REJECT`;调用 `docker exec <container_id> iptables ...` 注入;期望:单元测试 mock `subprocess`,验证生成规则序列正确
- [x] 3.2 添加 `is_domain_allowed(url: str, allowed_domains: list[str]) -> bool` 静态方法 —— 解析 URL host,在 allowed_domains 中精确匹配或后缀匹配(`*.example.com` 形式);期望:单元测试覆盖 `example.com` / `*.example.com` / 子域名 / 大小写 等 8 个 case
- [x] 3.3 在 `BuiltInToolExecutor` 调用 `is_domain_allowed` 在 `browser_navigate` 入口处 —— 不在白名单 → 返回 `domain_not_allowed` 错误,不调用 Playwright;期望:集成测试 mock `is_domain_allowed` 返回 False,验证 Playwright 未被调用

## 4. 内置工具注册(`src/hecate/services/tool/builtin.py`)

- [x] 4.1 在 `BUILTIN_TOOL_DEFINITIONS` 添加 6 个 browser_* 工具的 JSON Schema —— `browser_navigate` / `browser_click` / `browser_type` / `browser_extract` / `browser_screenshot` / `browser_fill_form`,每个带完整 `parameters` + `description`;期望:`list(BUILTIN_TOOL_DEFINITIONS.keys())` 包含全部 11 个工具(5 个旧的 + 6 个新的)
- [x] 4.2 在 `BuiltInToolExecutor.execute()` 添加 6 个 handler + `_get_or_create_session(context)` 助手 —— 每个 handler 从 context 提取 `session_id`,调用 `BrowserSessionManager.get_or_create(session_id).<method>()`;期望:单元测试 mock `BrowserSessionManager`,验证 6 个 handler 都被正确路由
- [x] 4.3 添加 `_check_browser_enabled()` 私有方法 —— 读取 `settings.AGENT_ENV_BACKEND`,如果 `local` 则所有 browser_* 直接返回 `{"error": "browser_disabled", "reason": "sandbox_required"}`;期望:单元测试覆盖 `local` / `docker` 两种配置
- [x] 4.4 把 6 个 browser_* handler 通过 `handler_dict` 字典添加到 `execute()` 路由表 —— 期望:`pytest tests/test_services/test_tool/test_builtin.py::test_execute_routes_all_builtin_tools` 通过(验证 11 个 tool 全部能 route 到 handler)

## 5. 风险门控与审计集成

- [x] 5.1 在 `BUILTIN_TOOL_DEFINITIONS` 每个 browser_* schema 添加非标准的 `risk_level: "MEDIUM"` 字段;在 `BuiltInToolExecutor.execute()` 入口读取并传给 9.4 的 risk gating hook;期望:单元测试 mock risk gating,验证收到 `risk_level="MEDIUM"`
- [x] 5.2 在 `browser_navigate` handler 内,调用 `is_domain_allowed` 后,**不在白名单时**临时把 risk_level 升级为 `"HIGH"` 再触发 gating;期望:集成测试验证非白名单域名触发 HIGH 分支
- [x] 5.3 验证 `ApprovalCallback` 在 risk="HIGH" 时被调用 —— 现有 ApprovalCallback 通过 ToolWorker 触发,不需要改代码;此处只需写一个测试验证 ToolDecisionModel 里有对应记录
- [x] 5.4 验证 `browser_extract` 的 text/html 返回 + `browser_screenshot` 的 image 都进入 DLP 管道(9.10) —— 在 handler 返回前调用 `dlp_service.scan()`;期望:单元测试 mock DLP,验证 scan 被调用 + 输出被替换
- [x] 5.5 添加 `tests/test_services/test_browser/test_audit.py` —— 验证 6 个 browser_* 调用后 ToolDecisionModel 都有对应记录(tool_name, args, risk_level, result_summary)

## 6. 端到端集成

- [x] 6.1 CI smoke test:`docker build -t hecate-browser-sandbox:latest docker/sandbox` 在 CI 环境中成功 —— 添加到 `.github/workflows/ci.yml` 的 `lint-and-build` job
- [x] 6.2 创建 `tests/test_services/test_browser/test_integration.py` —— 用 `pytest.mark.skipif(not has_browser())` 守护,有 Chromium 时跑真实 Playwright navigate → screenshot → extract;期望:本地有 Chromium 时通过,CI 跳过
- [x] 6.3 手动 E2E:启动本地服务,创建 agent session,执行 `browser_navigate("https://example.com")` → `browser_extract({})` → `browser_screenshot({})` 序列,验证返回内容;在 PR 描述里附截图
- [x] 6.4 跑全套 CI 检查:`ruff check src/hecate/ tests/` && `ruff format --check src/ tests/` && `mypy src/` && `python -m pytest tests/ -q` —— 期望:0 errors

## 7. 文档

- [x] 7.1 创建 `docs/how-to/browser-automation.md` —— 涵盖:启用条件(`AGENT_ENV_BACKEND=docker` + 镜像构建)、6 个工具的 LLM-facing 文档、域名白名单配置示例、headless/headful 选择、风险等级说明、DLP 行为、常见错误码(`domain_not_allowed` / `element_not_found` 等)、与 execute_code 的对比
- [x] 7.2 更新 `docs/features/feature-catalog.md` 第 318 行 `6.27` 行的描述 —— 在 ✅ 标记前补一行 "**Status: shipped 2026-XX-XX (PR #XX)**" + PR 链接
- [x] 7.3 更新 `docs/features/roadmap.md` 第 654 行 `- [ ] **6.27 Browser Automation Tool**` —— 改为 `- [x] **6.27 Browser Automation Tool** (PR #XX)`
- [x] 7.4 更新 `docs/features/p3-mvp-audit.md` 第 18 行"已完成 84 项"统计 —— 改为 85 项,在新表格行里记录 6.27 的交付摘要 + PR 链接
