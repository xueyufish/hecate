# Tasks: agent-plugins-ingestion

## 1. 数据模型与配置

- [x] 1.1 `PluginModel` 新增 `origin`(String, nullable)/`content_hash`(String, nullable)/`scan_result`(JSON, nullable)三列;`SkillModel` 新增 `origin`(String, nullable)/`plugin_id`(UUID FK→plugins.id, nullable);对应 ReadSchema 输出补字段
- [x] 1.2 alembic forward migration(纯新增 nullable 列,含 downgrade);更新 `tests/test_models/` 列断言
- [x] 1.3 `core/config.py` 新增:`AGENT_PLUGINS_INGESTION_ENABLED`(bool, 默认 false)、`PLATFORM_PLUGIN_INSTALLERS`(list[str], 默认空)、`AGENT_PLUGIN_STDIO_COMMAND_ALLOWLIST`(list[str], 默认 `["npx","uvx"]`)、`AGENT_PLUGIN_MAX_PACKAGE_MB`(默认 100)、`AGENT_PLUGIN_MAX_WORKSPACE_MB`(默认 500)、`AGENT_PLUGIN_RUNNER_IMAGE`(默认 `hecate-plugin-runner:latest`)、SaaS 模式标识项
- [x] 1.4 `SkillCreateSchema.source` 模式加 `plugin` 值,但 API 层拒绝手动传入(422,提示保留给摄取管道);`SkillUpdateSchema`/DELETE 对 `source="plugin"` 行返回 409

## 2. 校验与物化层(`plugin/agent_plugins.py` 骨架)

- [x] 2.1 模块骨架 + `validate_plugin_json()`:10 字段封闭集离线校验(未知顶层字段 warn+continue;required/name 语法/`$schema` 不识别/author 畸形 → 整包拒绝);单测覆盖全部分支
- [x] 2.2 源物化:dir 拷贝、git clone(记录 ref+commit SHA+content digest 三元组,排除 .git 计算 digest)、zip 安全解压(zip-slip 路径遏制 + 文件数上限)统一落到 `{PLUGINS_DIR}/agent-plugins/{name}/`;失败清理不留痕;单测
- [x] 2.3 路径遏制工具:所有触及路径 resolve 后必须落在包根内,symlink 逃逸拒绝;单测(含逃逸用例)
- [x] 2.4 体积上限检查(单包/每 workspace 累计,platform 级计入平台累计);超限拒绝并报测量值;单测
- [x] 2.5 bare SKILL.md 目录识别:无 plugin.json 但含 `*/SKILL.md` → 合成虚拟包身份(name=目录名,虚拟标记);单测

## 3. 组件发现与导入

- [x] 3.1 skills 发现器:`skills/` 仅直接子目录、子目录须含 SKILL.md、不递归;每组件 skip-and-continue,结果产出组件清单结构;单测
- [x] 3.2 SKILL.md 导入映射:复用 `parse_skill_md`;name≠目录名 → skip+warn(加严);`license/compatibility/metadata/allowed-tools` → JSON;`source="plugin"` + `origin` + `plugin_id` 就位;单测
- [x] 3.3 mcp.json 解析与翻译:三型条目校验(封闭变体、stdio 的 command/args/env/cwd、http 的 url/headers);`$schema` 与 plugin.json 不符 → 仅禁 MCP;headers 含凭证值 → 拒该条目;http 非 loopback 必须 HTTPS;sse→streamable-http 映射;单测
- [x] 3.4 撞名策略:同 origin 同名 → upsert;异 origin/与用户技能撞名 → 拒绝并列出冲突名;单测

## 4. 安装编排与生命周期(`PluginService` 扩展)

- [x] 4.1 安装编排:switch 检查 → 物化 → 校验 → 路径遏制 → 体积 → 发现 → 信任分派过滤 → 扫描槽位(no-op)→ 单事务持久化(PluginModel `type="agent-plugin"` 行 + skills 行);失败全清理;集成测试走通 dir/git(http 场景)
- [x] 4.2 `ScanStage` 协议 + no-op 实现(`scan_result` 恒 null),enable 重扫钩子同接口;单测
- [x] 4.3 重装 upsert:删旧 `plugin_id` 技能行 → 重导入 → 更新 version/origin/content_hash;单测
- [x] 4.4 卸载级联单事务:删 skills 行(by plugin_id)→ 注销 MCP → 删行 → 删目录;任一步失败回滚;单测
- [x] 4.5 启动孤儿清扫:无行目录移除 + 日志;单测
- [x] 4.6 虚拟包安装/卸载走同一编排路径;单测

## 5. MCP 投影与技能可见性

- [x] 5.1 enable/disable 投影:把 `mcp://` 单 entry 注册路径泛化为 manifest 组件批量注册,注册名 `<plugin-name>__<server>`,workspace 隔离沿用;disable/卸载注销全部前缀 server;单测 + 既有 MCP API 测试回归
- [x] 5.2 启动重放:startup 对所有 enabled agent-plugin 行重放注册(不连接);单测
- [x] 5.3 SkillLoader 过滤:`source="plugin"` 技能按所属插件 status 过滤,disabled → skip+warn;单测(disabled/enabled/卸载残留)
- [ ] 5.4 组件清单落库:manifest JSON 内 per-component outcome(skills/MCP server 各自 imported/skipped+原因);列表 API 透出;单测

## 6. stdio 沙箱路径(最后阶段,依赖 9.4c)

- [x] 6.1 命令 allowlist 执行前校验:command 必须 allowlist 内;检测会展开任意代码的 args/env 模式 → 拒该 server 条目;fail-closed(allowlist 配置非法即全部拒绝);单测
- [x] 6.2 沙箱执行桥接:`SandboxExecutor` 内启动 stdio 子进程,插件根只读挂载 `${PLUGIN_ROOT}`、每包数据卷 `${PLUGIN_DATA}`,占位符翻译;镜像走 `AGENT_PLUGIN_RUNNER_IMAGE`;策略应用失败拒绝启动;集成测试(docker 可用时跑,否则标记 skip)
- [x] 6.3 platform installer 鉴权:stdio 组件仅当安装者在 `PLATFORM_PLUGIN_INSTALLERS` 时落库(平台级行);SaaS 模式 skip-and-warn 记录;单测

## 7. API 与 CLI

- [x] 7.1 `POST /api/plugins/agent-plugins/install`(源描述符 `{type: dir|git|zip, location, ref?}`)→ 201 包摘要(身份/provenance/组件清单);switch 关闭时 feature-disabled 错误;API 测试
- [x] 7.2 既有 `GET /api/plugins` type 过滤对 agent-plugin 生效的回归测试;skill 列表/详情透出 provenance 字段测试
- [x] 7.3 CLI 子集:`hecate plugin install/uninstall/list --source dir|git|zip`;与 API 同一编排入口;CLI 测试

## 8. 文档与 catalog 记账

- [x] 8.1 feature-catalog 10.2 条目与 roadmap:记录 is_platform_admin 角色与 `plugin:install:*` 权限串推迟至 10.2(v1 由 config allowlist 承接)
- [ ] 8.2 feature-catalog 5.13a 参考列措辞修正("MCP registry Q4 2026 审计"→ registry 预览期不审计、责任在客户端);5.5c 参考列补 google/skills "plugins = Skills + MCP" 先例

## 9. 验证

- [x] 9.1 全量本地四件套:`ruff check src/hecate/ tests/` + `ruff format --check src/ tests/` + `mypy src/` + `python -m pytest tests/ -q` 零错误
- [x] 9.2 端到端冒烟:开关开 → dir 安装含 2 skills + 1 http MCP 的样例包 → 列表/enable/技能可见/MCP 注册 → disable → 重装 upsert → 卸载全清(以 pytest 集成测试形式固化)
