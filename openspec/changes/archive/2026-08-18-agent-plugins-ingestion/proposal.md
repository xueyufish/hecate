# Proposal: agent-plugins-ingestion

## Why

Agent Plugins 1.0 开放标准(agent-plugins.org,2026-08-06 发布,OpenAI/Microsoft/Amazon/Cursor/GitHub/Vercel 治理、Google 加入)已把"第三方插件 = plugin.json + skills/SKILL.md + mcp.json 的声明式包"收敛为行业事实格式,Codex/ChatGPT/VS Code/Cursor/Copilot/Kiro 均已 day-one 支持,而 SKILL.md 层(agentskills.io)已有约 48 个客户端。2026-08-17 全量调研确认:**所有被调研平台(IBM/Google/Salesforce/AWS/华为 AgentArts/阿里 AgentScope/Dify/deer-flow/Manus/美团 CatPaw)均未原生摄取 Agent Plugins 1.0**,均为 SKILL.md 与 MCP 分离接入——Hecate 落地 feature 5.5c 即成为首个把该标准作为原子安装单元的 self-hosted 多租户平台。同时,标准本身在 trust/权限/签名/审计上故意留白(FUTURE_CONSIDERATIONS.md),这正是 Hecate 已规划的 5.5c(信任分派)+ 5.13a(内容扫描,紧随本 change 串行)要填补的空间,方向与 ADR-029 信任分级内核架构一致。

## What Changes

- **新增 Agent Plugins 1.0 摄取管道**(单一 adapter 模块 `plugin/agent_plugins.py`,`PluginManifest` 与 8 类 ABC 不动):
  - 安装源三类:**本地目录 / git URL / zip(仅作传输,解压后等同目录)**,统一物化为受管安装目录的不可变快照;git 安装记录 ref+commit SHA+内容摘要三元组(pin-by-hash);v1 不支持私有仓库 PAT。
  - **Closed-manifest 校验**:plugin.json 10 个顶层字段封闭集,未知顶层字段 warn+continue,其余违规整包拒绝;`$schema` 版本不识别即拒绝,校验完全离线(不得联网取 schema)。
  - **固定位置发现**:`skills/` 仅扫直接子目录(不递归)、`mcp.json` 在根;组件级 skip-and-continue 失败语义;路径遏制(symlink 逃逸拒绝)。
  - **SKILL.md → SkillModel 导入**:复用现有 parser 与 SkillLoader token 预算;`source` 枚举新增 `plugin`,新增 `origin` 与 `plugin_id` 字段;接受 bare SKILL.md 目录(无 plugin.json,合成虚拟包记录)。
  - **mcp.json → MCP 注册**:http/sse 条目经现有连接池/熔断/重连注册(sse 映射到 streamable-http);注册名使用 `<plugin-name>__<server>` 前缀;stdio 条目按信任分派处理(见下)。
  - **组件级信任分派(ADR-029 对齐)**:skills(T4)+ http/sse MCP(T2)→ workspace admin 可装;stdio(本地子进程)→ 仅 self-hosted 平台级安装,经 config allowlist 指定的 platform installer 执行,且子进程运行在 9.4c 容器沙箱池内(命令 allowlist,默认 npx/uvx;fail-closed);SaaS 模式对 stdio 组件 skip-and-warn(整包不拒)。角色/权限串级方案(is_platform_admin、`plugin:install:*`)**推迟至 10.2 RBAC 增强**(本 change 负责把该推迟记录进 feature-catalog 与 roadmap)。
- **包记录与溯源**:复用 `PluginModel`(`type="agent-plugin"`),新增 `origin`/`content_hash`/`scan_result` 列;`manifest_` JSON 存 plugin.json 全文 + 组件清单;安装目录与 DB 行事务配对,启动清扫孤儿目录;单包 100MB / 每 workspace 500MB 体积上限(config 可调)。
- **运行语义**:enable 位是唯一事实源——disabled 时技能对 SkillLoader 隐藏、MCP 注销;重装 = 卸载+重装(同 origin upsert、异 origin 拒绝);启动时对 enabled 包重放 MCP 注册。
- **扫描槽位预留**:管道内置 no-op 扫描 stage 接口,`scan_result` 字段就位;内容扫描本体属 5.13a(其后串行)。
- **总开关**:config `AGENT_PLUGINS_INGESTION_ENABLED` 默认 false;5.13a 落地前不对外启用,上线后兼作应急 kill-switch。
- **交付面**:REST API + 最小 CLI 子集(`hecate plugin install/uninstall/list --source dir|git|zip`,为 12.0 marketplace v1 的 installer 复用);无新 UI。
- **范围排除**:`hecate plugin export` 与 `io.hecate/` 双格式收敛(5.5d)、git 索引市场(12.0)、`extensions` 命名空间语义读取、MCP 2026-07-28 方言迁移(5.4b)、独立 `MCPServerModel` 表(5.4b,本 change 以 PluginModel manifest 为持久载体)。

## Capabilities

### New Capabilities

- `agent-plugins-ingestion`:Agent Plugins 1.0 包的安装/校验/组件导入/信任分派/溯源/卸载级联全管道,含虚拟包(bare SKILL.md)、体积上限、总开关与扫描槽位。

### Modified Capabilities

- `skill-api`:SkillModel 扩展——`source` 枚举新增 `plugin` 值、新增 `origin` 与 `plugin_id` 字段,及插件派生技能的创建/撞名语义。
- `skill-loader`:技能可见性规则扩展——`source='plugin'` 的技能按所属插件 enable 状态过滤(单一事实源)。
- `mcp-connection-management`:注册来源扩展——插件 manifest 派生的多 server 注册(现有 mcp:// 单 entry 语义泛化)、`<plugin-name>__<server>` 命名、启动重放、卸载注销。
- `plugin-system`:PluginModel 持久化字段扩展(`origin`/`content_hash`/`scan_result`)与 `agent-plugin` 类型的 enable/disable 投影语义。

## Impact

- **代码**:`src/hecate/plugin/agent_plugins.py`(新,摄取 adapter)、`src/hecate/plugin/`(installer/cli 扩展)、`src/hecate/services/plugin/service.py`(安装/卸载/enable 编排)、`src/hecate/models/plugin.py` + `src/hecate/models/skill.py`(+alembic migration)、`src/hecate/services/skill/`(导入与可见性过滤)、`src/hecate/services/mcp/`(注册投影与重放)、`src/hecate/services/sandbox/`(stdio 执行接入)、`src/hecate/api/management/plugins.py`(+ `src/hecate/core/config.py` 新配置项)。
- **API**:`POST /api/plugins/agent-plugins/install`(源:dir/git/zip)、复用现有 plugin 列表/enable/disable/delete 端点(agent-plugin 类型纳入既有过滤)。
- **依赖**:前置 5.5 ✅ / 5.4c ✅ / 5.9 ✅ 全部就绪;stdio 路径依赖 9.4c 沙箱池 ✅;不新增第三方依赖(校验与解析纯标准库 + 现有 Pydantic/YAML 栈)。
- **后续联动**:5.13a(内容扫描,go-live 门)、5.5d(双格式收敛,依赖本 change 保留的原包目录)、12.0(marketplace v1 installer 复用本 change CLI)、10.2(角色/权限串推迟记录)。
- **文档**:archive 阶段修正 feature-catalog 5.13a 参考列("MCP registry Q4 2026 审计"查无出处)并补充 google/skills 先例。
