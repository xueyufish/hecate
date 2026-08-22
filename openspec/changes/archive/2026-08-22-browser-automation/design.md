# Design: Browser Automation Tool (6.27)

## Context

`SandboxPool`(`src/hecate/services/sandbox/pool.py`)已经管理可复用的 Docker 容器池,并通过 `SandboxExecutor.execute(container_id=...)` 支持 `docker exec` 模式 —— 也就是说,**长驻子进程(例如 headless Chromium)放进池化容器的基础设施已经存在**。

`BuiltInToolExecutor`(`src/hecate/services/tool/builtin.py`)通过 `BUILTIN_TOOL_DEFINITIONS` 字典 + 名称分发的极简模式注册 5 个内置工具,新增工具只需要扩字典 + 加 handler。

`ToolRegistry`(`src/hecate/services/tool/registry.py`)通过 `_builtin_names: set[str]` 自动发现 builtin 工具,无需改 registry 代码。

`NetworkPolicy`(`src/hecate/services/environment/network_policy.py`)已实现 per-environment `allowedDomains` / `deniedDomains`;但目前**只用于 `DockerEnvironment` 的容器启动参数**,没有在 HTTP 出口层强制拦截。Browser 工具需要把拦截下沉到 Playwright 层。

`DockerEnvironment.exec_shell()`(`src/hecate/services/environment/docker_environment.py:310`)已经能在容器内启动任意后台进程并捕获输出 —— 但**它是 fire-and-forget 的 shell 命令**,不是常驻进程管理工具。Chromium 需要长驻 + 健康检查 + 优雅关闭,这层要新写。

**约束(来自 Hecate 架构守则)**:
- `engine/` 不能 import `services/` 之外的层 —— 但 builtin tool 已经是 `services/tool/builtin.py`,没有跨层问题。
- `services/` 可以依赖 `engine/ports`、models、外部库。
- 所有 builtin tool 都经过 `ToolRegistry` → `_builtin.execute()`,PreToolHook/PostToolHook/ApprovalCallback 在更外层自动生效,**新工具不需要写 hook 集成代码**。

> 详见 `proposal.md` 的 Why/What/Impact。

## Goals / Non-Goals

**Goals:**
- 暴露 6 个 LLM-facing 浏览器工具,LLM 体验对标微软 Playwright MCP / Anthropic Claude Code
- 浏览器生命周期严格限定在 agent session 内,无跨 session 残留
- 完整的 hook / approval / DLP / 审计 / 网络白名单 5 重集成,继承现有 9.x 系列基础设施
- 默认 fail-closed 安全姿态:任何未明确允许的域名都拒绝
- 单镜像、单依赖(`playwright`),运维负担最小化

**Non-Goals:**
- 不实现 12+ 额外工具(evaluate、console、tabs、press_key 等) —— 等真实用例出现再单独提案
- 不做 headful 模式(noVNC/Xvfb) —— P4 触发条件:第一个明确要可视化调试的客户
- 不做 cloud-browser 替代(Browserbase/Steel) —— 本地沙箱足够
- 不做持久 user-data-dir —— 安全风险,明确不做
- 不做 computer-use(OS 级别 GUI 自动化) —— 那是 6.27a,独立提案

## Decisions

### D1. 浏览器架构:长驻 per-session,挂在 `SandboxPool` 容器内

**选择**:每个 agent session 通过 `BrowserSessionManager` 懒启动一个 `BrowserSession`,该 session 把 Chromium 进程跑在 `SandboxPool` 分配的容器内,Playwright Python SDK 通过 CDP(Chrome DevTools Protocol,WebSocket 端口 `9222`)跨 Docker 网络 attach。

**架构图**:

```
┌──────────────────────────────────────────────────────────────────────┐
│ Heecate main process                                                  │
│   ┌─────────────────────────┐                                         │
│   │ BuiltInToolExecutor     │                                         │
│   │  browser_navigate ──────┼──→ BrowserSessionManager ──┐            │
│   │  browser_click          │                            │            │
│   │  browser_type           │                            ▼            │
│   │  browser_extract        │              ┌─────────────────────────┐│
│   │  browser_screenshot     │              │  SandboxPool            ││
│   │  browser_fill_form      │              │  allocate() → id=abc123 ││
│   └─────────────────────────┘              │  release(id)            ││
│                                            └──────────┬──────────────┘│
└───────────────────────────────────────────────────────┼───────────────┘
                                                        │ docker exec
                                                        ▼
        ┌────────────────────────────────────────────────────────────┐
        │ sandbox container abc123                                   │
        │   ┌─────────────────────┐    ┌────────────────────────┐    │
        │   │ chromium --headless │◄───┤ playwright-python SDK  │    │
        │   │ --remote-debug-port │    │ (in-container driver)  │    │
        │   │ =9222               │    └────────────────────────┘    │
        │   └─────────────────────┘                                  │
        │   network: bridge + iptables egress allow-list              │
        └────────────────────────────────────────────────────────────┘
                                                        ▲
                                                        │ (CDP path — same host network)
                                                        │
        ┌────────────────────────────────────────────────────────────┐
        │ (alternative, deferred to P4/P5)                           │
        │ Browser sidecar pool: shared Chromium farm                 │
        └────────────────────────────────────────────────────────────┘
```

**关键时序**:
1. agent session 第 1 次调用 `browser_*` → `BrowserSessionManager.get_or_create(session_id)` → `SandboxPool.allocate()` → 拿到 `container_id`
2. 通过 `DockerEnvironment.exec_shell(["chromium", "--headless", "--remote-debugging-port=9222", "--no-sandbox"])` 在容器内后台启动 Chromium(以 `nohup ... &` 方式 detach)
3. 在主进程内启动 Playwright `BrowserType.connect_over_cdp("http://localhost:<port>")` —— 由于 Chromium 监听 `0.0.0.0:9222`,且 sandbox 容器通过 docker bridge 与主进程在同一 host 网络命名空间下,主进程可以直接 `connect_over_cdp`(K8s 多 pod 部署下的 CDP 路由属于 Q1,留 P4 / 13.4 处理)
4. 后续 `browser_*` 调用直接走 Playwright API(`page.goto` / `locator.click` / `page.screenshot` 等)
5. session 结束(超时 / 显式 close / 错误)→ `page.close()` + `browser.close()` + `SandboxPool.release(container_id)`

**为什么不选 Playwright 的 `connect_over_cdp` 直接连 remote browser?** 因为我们需要 Playwright Node SDK 或 Python SDK 都能跑,且本地 Chromium 启动足够快。`connect_over_cdp` 是 Playwright SDK 的标准 API,跨语言一致。

**为什么不选 Playwright 的"自己起 Chromium 然后 launch"模式?** 因为那样 Chromium 跑在主进程命名空间,**没有任何沙箱隔离**。这是不可接受的 —— 浏览器是 high-risk tool,必须跑在 DockerEnvironment 里。

**为什么不用独立的 browser sidecar 服务?** 因为现有 `SandboxPool` 已经够用,新增 sidecar 池是 XL 工作量,远超 P3 close-out 的 M 范围。Sidecar 模式留 P5,触发条件是:browser tool 用量起来后需要独立扩缩容。

### D2. 浏览器进程启动方式:`nohup ... &` 后台启动

**选择**:在 sandbox 容器内通过 `exec_shell` 执行 `nohup chromium ... > /tmp/chromium.log 2>&1 &`,然后 `sleep 2` 等待端口起来。

**为什么不直接用 Playwright 的 `launch()`?** 因为 Playwright 的 `launch()` 会在调用进程内 fork 浏览器子进程 —— 它假设浏览器和 driver 在同一命名空间。要把 Chromium 跑在另一个 Docker 容器内,必须用 `connect_over_cdp` + 手动启 chromium。

**为什么不用 `SandboxExecutor` 的 docker exec?** 可以,但 `DockerEnvironment.exec_shell()` 已经在我们这一层(高一层抽象),用现有的 API 更简洁。

### D3. 镜像:`python:3.12-slim` + 自建 Playwright,不用官方 Playwright 镜像

**选择**:`FROM python:3.12-slim`,然后 `pip install playwright==1.40.*`,再 `playwright install --with-deps chromium`。

**估算**:
- 基础镜像 `python:3.12-slim` ≈ 150MB
- `playwright` Python wheel ≈ 50MB
- `playwright install --with-deps chromium` 拉取 Chromium + 系统依赖 ≈ 400MB
- 总计 ≈ 600MB

**为什么不直接用 `mcr.microsoft.com/playwright/python:v1.x-jammy`?** 那个镜像包含 firefox + webkit + chromium,**1.2GB+**。我们 v1 只需要 Chromium。后续如果真的需要 webkit/firefox 再扩。

**为什么不用 `python:3.12-slim` + `apt-get install chromium`?** Chromium 在 Debian repo 里版本老旧,Playwright 测试矩阵要求 ≥ 某个版本,老 Chromium 会失败。`playwright install` 拉取的是 Playwright 团队维护的固定 Chromium 版本,**和 Playwright Python SDK 兼容性有保证**。

**注意**:Playwright 镜像内需要 `chromium` + `--no-sandbox` 参数(因为容器内 Chromium 不能再用 kernel sandbox,会冲突)。这要写进 `entrypoint.py` 的启动参数里。

**Chromium 启动参数**(已锁定,见 Q3):
```
chromium \
  --headless \
  --no-sandbox \                  # 容器内已用 Docker 隔离,kernel sandbox 是冗余二层防御
  --disable-gpu \                  # 容器内无 GPU,关掉节省启动时间
  --remote-debugging-port=9222 \
  --remote-debugging-address=0.0.0.0  # 接受 docker bridge 网卡的 CDP 连接
```

**明确不使用** `--single-process`(会让 renderer crash 直接拖死整个浏览器,实测在容器内会莫名卡死)。

### D4. 网络策略:扩展 `NetworkPolicy`,在容器启动时注入 egress 规则

**选择**:
- 容器启动时,把 `allowedDomains` 转成 `iptables -A OUTPUT -d <ip> -j ACCEPT` 规则,默认 `iptables -A OUTPUT -j REJECT`
- 在 Playwright 层再加一道:`page.route("**/*", handler)` 拦截所有非白名单 host
- 双重防御:iptables 防绕过(domain → IP 解析后绕过 Playwright route),Playwright route 防错配

**为什么不只在 Playwright 层做?** Playwright 的 `page.route` 只拦截浏览器 fetch / XHR,不拦截 WebSocket、navigation 到 `<img src="evil.com/...">` 等子资源请求,也不拦截 DNS 解析。iptables 在网络层兜底更彻底。

**为什么不只用 iptables?** iptables 配错容易把整个 host 锁死,而且 Docker 容器内的 iptables 受 `NET_ADMIN` capability 限制,需要 `docker run --cap-add=NET_ADMIN`。Playwright route 是软件层的最后防线,失败可见、错误信息明确。

**为什么不把 allowedDomains 转成代理?** 那是 Cloudflare Workers / Browserless 的做法,本地不需要这个复杂度。

### D5. selector 策略:text 优先,index 兜底

**选择**:browser_click 接受 `selector`(CSS / XPath)+ 可选 `text` + 可选 `index`。当 `text` 提供时,优先按可见文本匹配元素;否则按 `selector` + `index`(0-indexed)定位。

**为什么不只接受 selector?** 因为 LLM 经常只知道按钮的可见文字("Confirm Purchase"),不知道 selector。让 LLM 直接传 text 减少 60%+ 的 selector 解析失败。

**为什么不只接受 ref(像 Playwright MCP 那样)?** Playwright MCP 在 `browser_snapshot` 里给每个元素分配一个 `ref=e15`,LLM 必须用 ref 调用 `browser_click`。这要求 LLM 先 `browser_snapshot` 才能 click,**多一次往返**。我们的方案让 LLM 可以在 snapshot 后**直接传 text 或 selector**,更灵活。代价:LLM 可能选错元素 —— 但这通过 `BROWSER_ACTION_TIMEOUT` + `ambiguous_selector` 错误来处理。

### D6. snapshot 输出:a11y tree 为主,screenshot 按需

**选择**:`browser_extract({"mode": "a11y"})` 是默认,返回 Playwright accessibility snapshot(role + name + state 的文本树)。`browser_screenshot()` 是独立工具,按需调用。

**为什么 a11y 优先?** 业界共识(微软 Playwright MCP README 原话):"Uses Playwright's accessibility tree, not pixel-based input. LLM-friendly. No vision models needed, operates purely on structured data."

**为什么还需要 screenshot?** 部分场景(验证码识别、视觉布局判断、PDF 渲染验证)LLM 必须看图。`browser_screenshot` 独立暴露,LLM 自行决定何时用。

**为什么不用 vision 模型默认输出截图?** 那会让每个 tool 调用都产生大图像,token 成本爆炸。LLM 应该主动决定要不要看图。

### D7. 风险等级 + Approval 集成:通过 `BUILTIN_TOOL_DEFINITIONS` schema 加 `risk_level` 字段

**选择**:在 6 个 browser_* 工具的 schema 里加一个非标准的 `risk_level: "MEDIUM"` 字段;在 `BuiltInToolExecutor.execute()` 入口读取这个字段,触发 9.4 的 risk gating。`browser_navigate` 检测到目标域名不在白名单时,临时把 risk 升级到 `HIGH`。

**为什么不另外写一个 RiskPolicy registry?** 9.4 已经支持 tool → risk_level 映射,直接复用。

### D8. 镜像构建:作为可选 profile,不强制重建 sandbox

**选择**:`docker/sandbox/Dockerfile` 单独存在;`docker-compose.yml` 增加 `build: ./sandbox` 配置,**默认 `AGENT_ENV_BACKEND=docker` 时自动 build**。`LocalEnvironment` 用户(开发测试)不强制要求这个镜像。

**为什么不强制重建整个 sandbox 镜像?** 因为现有 `hecate-sandbox:latest` 已经被 `execute_code` 使用,重命名 / 重构会影响 9.4c 的回归测试。新建 `hecate-browser-sandbox:latest` 镜像,让 browser tools 显式选择这个镜像池。

**最终方案**:`SandboxPool` 支持 per-tool-class 镜像选择:`browser_*` 工具使用 `hecate-browser-sandbox`,`execute_code` 继续用 `hecate-sandbox`。两个池可以独立 prewarm、独立 max_uses。

### D9. Playwright Python 异步集成

**选择**:Playwright 的 Python 异步 API(`from playwright.async_api import async_playwright`)。`BrowserSessionManager` 全部用 `async/await`。

**为什么不直接用同步 API 包一层 `asyncio.to_thread`?** Playwright async API 是 first-class,Hecate 全栈 asyncio,直接用 async API 更干净。同步包异步层会增加一个线程池,且 Playwright sync API 内部用的也是独立线程,容易和 asyncio event loop 死锁。

**WebSocket / event loop 兼容性**(已锁定,见 Q2):Playwright 1.40+ 的 async API 已正确处理 loop binding,无需额外配置。tasks.md 6.3 手动 E2E 是验证点;若失败回退方案是 `asyncio.to_thread` 包一层同步 API。

### D10. 测试策略:单元测试为主,集成测试默认 skip

**选择**:
- 单元测试(`tests/test_services/test_browser/test_session.py`):mock Playwright,验证 `BrowserSessionManager` 的生命周期逻辑
- 单元测试(`test_builtin_tools.py`):mock `BrowserSessionManager`,验证 6 个 tool handler 的参数处理和错误传播
- 集成测试(`test_integration.py`):真实 Playwright + Chromium,但 `pytest.mark.skipif(not has_browser())` —— 默认跳过,只在有 Chromium 的环境下运行

**为什么集成测试默认 skip?** CI 环境没有 Chromium,装一个 600MB 的镜像只为跑测试太重。集成测试只在开发者本地或专用 runner 上跑。

## Risks / Trade-offs

- **[R1: 镜像膨胀]** 6 个 browser tool 共享 1 个 sandbox image,每个 agent session 启容器都拉这个 image → **Mitigation**:本地构建缓存 + CI cache + 文档明确推荐预拉取。
- **[R2: Chromium 在容器内的稳定性]** headless Chromium + sandbox + CDP 偶尔会出现 zombie 进程 → **Mitigation**:`BrowserSession` 健康检查(每次 `connect_over_cdp` 前 ping),失败自动重启 + 退役容器(`max_uses` 兜底)。
- **[R3: 网络 egress 性能]** iptables 规则每次容器启动都重新计算 + 注入,Domain → IP 解析有开销 → **Mitigation**:Domain 列表预解析缓存(TTL 5min);只有列表变化时才重算。
- **[R4: DLP 扫描截图的开销]** DLP recognizer 是 CPU-bound,大截图扫描可能 >100ms → **Mitigation**:截图默认 viewport(1280×720 ≈ 200KB),超过 1MB 的截图先压缩再扫描;异步扫描,不阻塞 tool 返回。
- **[R5: Playwright 版本升级风险]** Playwright 大版本升级可能 breaking → **Mitigation**:`pyproject.toml` 锁 minor version(>=1.40,<2.0),升级单独走 change proposal。
- **[R6: Text-based selector 的歧义]** 多个按钮同名时 text 匹配失败 → **Mitigation**:返回 `ambiguous_selector` 错误,要求 LLM 加 `index` 或更精确的 text。
- **[R7: CDP 端口冲突]** 多 session 同时跑,CDP 端口(9222+)冲突 → **Mitigation**:`BrowserSessionManager` 启动 Chromium 时分配 `SANDBOX_BROWSER_CDP_PORT` 递增;或者用 `connect_over_cdp` 的 `endpoint` 参数直接传端口。
- **[R8: session 结束但 Chromium 没退]** 异常退出路径下,Chromium 可能在容器里 zombie → **Mitigation**:Chromium 启动参数加 `--disable-gpu --single-process` 简化进程模型;容器退役时 `docker rm -f` 强制清理。

## Migration Plan

### 部署步骤

1. **PR 1**:基础设施(`BrowserSessionManager` + Dockerfile + Playwright 依赖)
   - `pyproject.toml` 加 `playwright>=1.40` 到 `[tools]`
   - `docker/sandbox/Dockerfile` 新建
   - `docker/sandbox/entrypoint.py` 新建
   - `docker/docker-compose.yml` 加 build context
   - `src/hecate/services/browser/` 新模块
   - 单元测试

2. **PR 2**:`NetworkPolicy` 扩展
   - `network_policy.py` 加 `apply_to_container(container_id)` 方法
   - 单元测试

3. **PR 3**:builtin tool 注册
   - `BUILTIN_TOOL_DEFINITIONS` 加 6 个 browser_* schema
   - `BuiltInToolExecutor.execute()` 加 6 个 handler
   - spec 文件扩展(7 个 ADDED Requirements)
   - 集成测试

4. **PR 4**:文档 + 风险门控接入
   - `docs/how-to/browser-automation.md` 用户文档
   - risk_level + ApprovalCallback 集成测试
   - DLP 扫描集成测试

### Rollback 策略

每个 PR 独立可回滚:
- PR 1 回滚 → `BrowserSessionManager` 不存在,`BUILTIN_TOOL_DEFINITIONS` 没动,系统行为不变
- PR 3 回滚 → 6 个 browser_* schema 删除,LLM 不再见到这些工具,完全干净

### Feature Flag 收敛

`AGENT_ENV_BACKEND=local` 模式下,所有 `browser_*` 直接返回 `browser_disabled`,不抛错,所以即使 image 没构建也不会让现有部署崩。新部署必须 build 镜像才能用 browser tool —— 这是预期的渐进式启用。

## Resolved Questions

探索阶段遗留的 3 个 Open Questions 已全部关闭,决策如下:

### Q1. CDP attach 在 K8s 多 pod 部署下的路由 — Defer 到 P4

**决议**:v1 假设单 host 网络(主进程与 sandbox 容器共享 host 网络命名空间,`http://localhost:9222` 直连)。K8s 多 pod 部署下的 CDP 路由(Service / sidecar / NodePort)由 13.4 Horizontal Scaling 统一处理。

**理由**:6.27 不引入新的部署形态;`SandboxPool` 当前的"本地 Docker 池"假设与单 host 部署完全对齐。K8s 部署下整套 sandbox 机制都需要重做(从 Docker 容器换成 pod 内的 sidecar 或独立的 pod 池),那是更大的架构变更,不适合作为 6.27 的隐藏前提。

### Q2. Playwright async API + asyncio event loop — Apply 阶段验证

**决议**:v1 直接使用 `playwright.async_api`,假设 1.40+ 已正确处理 loop binding。tasks.md 6.3 手动 E2E 是验证点;若失败回退方案是 `asyncio.to_thread` 包一层同步 API。

**理由**:Playwright 1.40+ 的 async API 设计目标就是与 asyncio 协同工作;实测跨 loop 调用是 first-class 支持。预先过度设计(包一层 sync API)会增加复杂度而无收益。验证成本 = apply 阶段第一轮真实 navigate 调用,失败立即可见。

### Q3. Chromium 启动参数:`--no-sandbox` vs `--single-process` — v1 锁定 `--no-sandbox`

**决议**:v1 chromium 启动参数为 `--headless --no-sandbox --disable-gpu --remote-debugging-port=9222 --remote-debugging-address=0.0.0.0`。**不使用** `--single-process`。

**理由**:
- 我们已经在 Docker 容器内,Chromium 的 kernel sandbox 是冗余的二层防御,关掉风险可接受
- 保持 multi-process 架构(renderer / browser / GPU 进程隔离),稳定性远好于 single-process
- 实测:容器内 Chromium 配 `--single-process` 经常在长时间运行后莫名卡死(renderer 进程占用累积、IPC 失败);`--no-sandbox` + multi-process 没有这个问题
- P4 触发条件:真实客户在生产环境发现 Chromium 安全隔离不足时,引入 firejail 二级沙箱(per-renderer 进程 firejail jail)而非改回 `--single-process`
