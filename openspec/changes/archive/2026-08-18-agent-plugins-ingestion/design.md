# Design: agent-plugins-ingestion

## Context

现状与约束(探索阶段已核实):

- **插件子系统**:`src/hecate/plugin/` 已有 manifest(loader 只认 `plugin.yaml`)、registry、installer(`.hecate-plugin` ZIP)、`PluginService`(DB 编排,enable 时对 `mcp://` entry 注册单 server)。`PluginModel` 列:`id/name/type/version/status/entry/manifest_(JSON)/config/workspace_id` + 时间戳。
- **技能子系统**:`SkillModel` 有 `source`(String(20),值约束在 Pydantic 层 `system|user|project`)、`allowed_tools/metadata_/scripts/references`(JSON)、唯一索引 `idx_skills_workspace_name (workspace_id, name, deleted, deleted_at)`;`parse_skill_md` 可复用;SkillLoader 的 token 预算对入库技能自动生效。
- **MCP 子系统**:`MCPServerRegistry` **纯内存态**(重启即丢,存量缺口);`MCPClientManager` 已有连接池/熔断/重连/TTL 工具缓存;`register(name, endpoint, transport, workspace_id)` 支持 workspace 隔离。
- **沙箱**:`SandboxPool`/`SandboxExecutor`(docker run/exec,默认镜像 `hecate-sandbox:latest`,network=none、read_only_fs=True)。
- **RBAC 现实**:只有 workspace 级 `WorkspaceRole`(ADMIN/EDITOR/VIEWER),**无**平台级 admin、**无**细粒度权限串。
- **标准事实**(spec 1.0.0,2026-08-06):plugin.json 10 字段封闭集;skills/ 仅直接子目录;mcp.json 三型(stdio/streamable-http/sse-deprecated);`${PLUGIN_ROOT}`/`${PLUGIN_DATA}` 占位符仅限 stdio 的 args/env/cwd;headers 禁凭证;mcp.json `$schema` 与 plugin.json 不符仅禁 MCP 组件;分发/信任/签名/审计全部留给客户端。

## Goals / Non-Goals

**Goals:**

- 一条端到端摄取管道:源物化 → 离线校验 → 组件发现 → 信任分派 → 扫描槽位(no-op)→ 持久化投影,全链路组件级失败边界。
- 复用而非重建:parser、SkillLoader、MCP 连接管理、PluginService 编排、SandboxPool 全部走既有路径。
- 为 5.13a(扫描)、5.5d(双格式)、12.0(installer)预留接口而不实现它们。

**Non-Goals:**

- 内容扫描本体(5.13a)、签名/摘要信任链(5.13)、`hecate plugin export` 与 `io.hecate/` 命名空间(5.5d)、git 索引目录与市场 UI(12.0)、`MCPServerModel` 独立表与 MCP 2026-07-28 方言迁移(5.4b)、org-admin 角色与 `plugin:install:*` 权限串(10.2)、私有 git 仓库 PAT、skills 递归发现、`extensions` 命名空间语义读取(原样保留在快照中)。

## Decisions

### D1. 单一 adapter 模块 `plugin/agent_plugins.py`
`PluginManifest`、loader、8 类 ABC 一概不动。新模块内含:plugin.json 封闭校验器、组件发现器、SKILL.md→SkillModel 映射、mcp.json→注册描述的翻译。
**理由**:标准发布仅 11 天(无 1.0.1/1.1),格式波动风险真实存在;单模块是最小的对冲面。**备选**(否决):扩展 `loader.py` 读双格式——污染已冻结的 5.5 契约。

### D2. 包记录复用 `PluginModel`(type=`"agent-plugin"`),加 3 列
`origin`(源描述符,git 安装含 ref+SHA+digest 三元组)、`content_hash`、`scan_result`(JSON,nullable,5.13a 前恒 null)。`manifest_` JSON = plugin.json 全文 + 组件清单(每个 skill/MCP server 的导入结果)。`entry=""`、`config={}`。
**理由**:数据、生命周期、数量级与现有插件行三同构(每 workspace 5~50 行);enable/disable/CRUD/列表/workspace 隔离全部现成;业界同构先例 = Dify `plugin_unique_identifier`(identity+version+checksum)、Hermes `.hub/lock.json`、Claude Code `known_marketplaces.json`+SHA-256 pin——都是"安装时固化 provenance+hash+结论",映射为字段组即可。
**备选**(否决):新表 `AgentPluginInstallModel`——两套 CRUD/API/enable 语义,12.0 与管理页反而要做 UNION;两个死字段(`entry`/`config`)的代价不成比例。

### D3. MCP 持久化 = PluginModel manifest 载体 + 启动重放
enable/disable 时把 manifest 内的 mcp.json 组件投影到内存 registry(把现有 `mcp://` 单 entry 注册路径泛化为 N server);启动时对所有 enabled 的 agent-plugin 重放注册。注册名 `<plugin-name>__<server>`,workspace 隔离沿用 `ServerInfo.workspace_id`。健康/熔断/池统计照旧只在内存。
**理由**:最小改动闭环本 change;registry 内存态是 5.4b 的存量缺口,不绑架本 change。
**备选**(否决,记入 5.4b):新 `MCPServerModel` 表——它要解决的主体是"手动注册的 server 也重启即丢",与本 change 正交;AgentCore Gateway 式注册时 `tools/list` 索引同理推迟。

### D4. 安装权限 v1 = config allowlist,角色/权限串推迟 10.2
新配置:`AGENT_PLUGINS_INGESTION_ENABLED`(默认 false,总开关/应急 kill-switch)、`PLATFORM_PLUGIN_INSTALLERS: list[str]`(邮箱 allowlist,空=无人可装 stdio)、`AGENT_PLUGIN_STDIO_COMMAND_ALLOWLIST: list[str]`(默认 `["npx","uvx"]`)、`AGENT_PLUGIN_MAX_PACKAGE_MB=100`、`AGENT_PLUGIN_MAX_WORKSPACE_MB=500`。
**理由**:代码里没有 org/platform admin 角色,为一个安装入口先建角色体系不值;OpenClaw 的来源分级摩擦(`--force`)与 Claude Code 的 managed-settings 企业策略键是同构先例。推迟项(is_platform_admin、`plugin:install:*`)在 apply 阶段写入 feature-catalog 10.2 条目与 roadmap。

### D5. 组件级信任分派矩阵(ADR-029 对齐)

| 组件 | 层级 | 安装者 | 执行位置 |
|---|---|---|---|
| skills/ 内容 | T4 纯数据 | workspace admin | SkillModel 行,受 SkillLoader 预算约束 |
| mcp.json http/sse | T2 网络边界 | workspace admin | 现有 MCP 连接池(惰性连接) |
| mcp.json stdio | T1 本地子进程 | platform installer(config allowlist) | 9.4c 容器沙箱 |

SaaS 模式(config 标识)或安装者不在 allowlist 时:stdio 条目 **skip-and-warn**(记录进组件清单),整包不拒——与 spec 的组件级失败边界同构,拒绝发生在安装权限层而非解析层。AgentArts"平台内部下载并启动 NPX/UVX"正是反面教材:不受控的安装期代码执行。

### D6. stdio 沙箱执行:SandboxExecutor + 命令 allowlist + fail-closed
- stdio server 的 `command` 必须在 allowlist(默认 npx/uvx);会展开为任意代码执行的 `args`/`env`(如 `bash -c`、内联脚本)拒绝该 server 条目。
- 子进程经 `SandboxExecutor` 在容器内启动;插件根只读挂载为 `${PLUGIN_ROOT}`,每包数据卷挂载为 `${PLUGIN_DATA}`(spec 占位符仅出现在 args/env/cwd,翻译发生在容器内命令构造前)。
- 镜像:新配置 `AGENT_PLUGIN_RUNNER_IMAGE`(默认 `hecate-plugin-runner:latest`,含 Node+Python 运行时的派生镜像;现有 `hecate-sandbox:latest` 不含 node)。
- **fail-closed**:沙箱策略应用失败(如 allowlist 配置非法、镜像缺失)→ 拒绝启动并记录,绝不降级为宿主进程。Codex 0.147(策略失败即拒网络)与 deer-flow(命令 allowlist)双先例。
- MCP 客户端侧:stdio 连接本来走单连接(非池化),容器内进程的 stdin/stdout 经 docker exec 桥接。
**备选**(否决):宿主直接 spawn——违背 ADR-029"运行时获取的制品永不 T0"。

### D7. 管道阶段序与失败边界
```
switch 关? →→ 拒绝(404 语义)
  ↓
源物化(dir 拷贝 / git clone / zip 解压)→ 受管目录不可变快照
  ↓  失败:整包拒绝,不留痕
plugin.json 封闭校验(离线;$schema 不识别=拒;未知顶层字段=warn)
  ↓  失败:整包拒绝
路径遏制检查(所有触及路径 resolve 后必须落在包根内;symlink 逃逸拒绝)
  ↓
体积上限(单包/累计)
  ↓  超限:整包拒绝,报测量值
组件发现:skills/(直接子目录)+ mcp.json(根)
  ↓  每组件 skip-and-continue,结果记入组件清单
信任分派过滤(stdio 按 D5)
  ↓
扫描槽位(no-op;5.13a 后在此 fail-closed)
  ↓
持久化投影(单事务):PluginModel 行 + skills 行 + (enable 时)MCP 注册
```
`mcp.json $schema` 与 plugin.json 不符 → 仅 MCP 组件禁用(skills 照导)——spec 原文语义。

### D8. SKILL.md 映射与 name=dir 加严
frontmatter `name`/`description` → 现有字段;body → `instructions`;`license`/`compatibility`/`metadata`/`allowed-tools` → JSON 列;`source="plugin"`、`origin`、`plugin_id` 就位。**加严**:frontmatter name ≠ 目录名 → skip 该技能(warn)。spec 本身不要求两者一致(委托 Agent Skills 规范);Hecate 作为多租户平台钉死"目录名=技能身份"以获得稳定的卸载/引用键——这是有意的超集严格性(dsh 的 fail-closed 字段校验同哲学)。

### D9. 撞名与重装
唯一索引 `idx_skills_workspace_name` 之下:同 origin 同名重装 → upsert(先删该 plugin_id 的旧行再导入);同名不同 origin(含用户手建技能)→ 拒绝安装并列出冲突名。升级 = 卸载+重装语义(无 diff 应用)。
**备选**(否决):AgentScope 式数字后缀——不可预测,Hermes 式 origin-hash 保护已由"同 origin upsert"覆盖更简单的场景。

### D10. enable = 单一事实源投影
PluginModel.status 是唯一开关:enabled → MCP 已注册 + skills 可见;disabled → 注销 + 隐藏(SkillLoader 按 plugin 状态过滤)。deer-flow 的 enabled-only 沙箱投影同构——不存在"技能可见但工具不在"的中间态。

### D11. 扫描槽位与总开关
`ScanStage` 协议:`scan(package_tree) -> ScanResult | None`,v1 注册 no-op 实现,`scan_result` 恒 null;enable 时重扫钩子同接口。5.13a 落地即替换实现并把 go-live 门接上。总开关 `AGENT_PLUGINS_INGESTION_ENABLED` 默认 false——5.5c 合并后不对外可用,5.13a 之后再置 true;此后兼作应急 kill-switch。

### D12. 目录布局与事务配对
受管目录:`{PLUGINS_DIR}/agent-plugins/{name}/`(现有 plugin.yaml 发现扫描对其无感——无 plugin.yaml 即跳过)。目录与 DB 行同生共死:安装 = 物化目录 + 建行(同一事务边界内尽力配对,失败即清理);卸载 = 单事务(删 skills 行、注销 MCP、删行、删目录),任一步失败整体回滚;启动时清扫无行孤儿目录。

### D13. git pin 三元组
origin = `{type: "git", url, ref, commit_sha, content_digest}`;`content_digest` 为物化树的内容哈希(排除 .git)。重装比对 origin.url + name 判同源。v1 仅公有仓库(clone 无凭证)。

### D14. 交付面:API + 最小 CLI,无新 UI
`POST /api/plugins/agent-plugins/install`(源描述符)+ 既有 plugin 端点(type 过滤即得列表/enable/disable/delete)。CLI:`hecate plugin install/uninstall/list --source dir|git|zip`——12.0 marketplace v1 的 installer 直接复用此子集,避免 12.0 反向修改 5.5c。前端零改动(管理页 type 过滤已支持)。

## Risks / Trade-offs

- [标准演进(1.0.1/1.1 可能新增组件类型)] → 单 adapter 模块(D1)+ 封闭校验对未知内容 fail 拒绝;升级面收敛在一个文件。
- [zip 解压攻击(zip-slip / zip 炸弹)] → 解压前路径遏制(resolve 后必须在包根内)+ 体积上限(D12 的 100MB/500MB)+ 文件数上限(实现细节,进 tasks)。
- [MCP 2026-07-28 方言迁移未完成] → 本 change 只摄取 mcp.json 配置,协议方言由 5.4b 的客户端迁移承接;摄取层不解析 body 做路由。
- [标准无签名字段,供应链风险前移到 5.13a] → 总开关默认关(D11):5.5c 合并 ≠ 可用;5.13a 规则引擎(含隐形 Unicode 检测)是 go-live 硬门;Snyk 基线(36% 注入率)已证明该顺序的必要性。
- [启动重放风暴:大量 enabled 包同时注册] → 注册本就不连接(惰性连接既有语义),重放只写 registry 条目,O(N) 字典操作,无风暴面。
- [stdio 桥接复杂度(docker exec + stdio JSON-RPC)] → 作为独立阶段排在 dir/git/http 路径之后交付(D5 决策);桥接层失败即 fail-closed 拒启,不影响其余组件。
- [SkillModel 加列的迁移] → 纯新增 nullable 列(origin/plugin_id),alembic 单次 forward migration,无回填;回滚 = 开关关 + 列留存(无害)。
- [12.0 反向依赖风险] → CLI 子集(D14)先行定义安装源描述符的稳定形状(`type/location/ref`),12.0 只消费不重定义。

## Migration Plan

1. alembic migration:`plugins` 表加 `origin/content_hash/scan_result`(nullable);`skills` 表加 `origin`(String, nullable)/`plugin_id`(UUID FK, nullable)。
2. 部署:默认配置下所有新开关关闭,行为与现状完全一致;受管目录 `agent-plugins/` 惰性创建。
3. 回滚:配置回退 + migration downgrade;已安装包(若有)随 downgrade 遗留目录由启动清扫移除(无行即孤儿)。

## Open Questions

- `hecate-plugin-runner` 镜像的具体构成(Node/Python 版本钉子、体积预算)在实现期定,不影响接口形状。
- 组件清单 JSON 的字段命名在实现期对齐 `scan_result` 的 5.13a schema 时统一,当前以 spec 的"per-component outcome"为准。
