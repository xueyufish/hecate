# Proposal

## Why

5.5c 之后平台并存两套插件格式：生态侧 Agent Plugins 1.0（`plugin.json` + `skills/` + `mcp.json`，只读摄入）与私侧 `.hecate-plugin`（`plugin.yaml` + Python 源码，ZIP 强制安装）。同一个能力的作者要维护两份包；workspace 沉淀的 skills 无法回流生态（其他生态客户端无法使用）；ZIP 强制与 5.5c 已落地的 dir/git 物化管线形成两套语义。Agent Plugins 1.0 规范 §5.6/§8.2 的 `extensions` 逃生舱（客户端命名空间字段 + 同名顶层目录，未实现该命名空间的客户端必须忽略）让一个包可以同时是合规范例和 Hecate 深度集成插件——收敛机制由规范本身背书，无需发明第三种格式。

## What Changes

- **读侧收敛（namespace 识别）**：摄入管线在 plugin.json 校验后识别 `extensions[AGENT_PLUGIN_NAMESPACE]` 与同名顶层目录 `io.github.xueyufish/`；目录内 `plugin.yaml` 承载 Hecate 私有清单（type/entry/permissions/config_schema）；plugin.json 为唯一身份来源——plugin.yaml 的 name/version 与之冲突即拒绝（replace-not-merge 语义）；其他命名空间按规范静默忽略。"一个包 = 一条 PluginModel 行"不变式保持。
- **双格式代码组件**：entry 引用的 Python 载荷随私有内容一起迁入 namespace 目录（§8.2 唯一被规范祝福的私有内容位置，open face 保持纯净）；组件级信任调度扩展为——包安装权限 = 最高组件 tier（code entry → T0 平台闸门，复用 `PythonEntryPolicy`；stdio → T1 沙箱；http-MCP → T2；skills → T4）；enable 时代码组件经既有 loader 注册，startup replay 覆盖。
- **写侧收敛（dual-format 输出）**：`hecate plugin package` 输出 Agent Plugins 布局——生成 plugin.json、plugin.yaml 迁入 namespace 目录；open face 为空（无 skills/mcp.json）也统一输出（规范允许组件缺省）；旧 `.hecate-plugin` 布局的安装兼容保留（additive，不破坏已分发 bundle）。
- **ZIP 降级**：Hecate 插件安装新增 directory/git 源（复用 5.5c 物化机制），ZIP 降为 transport-only——与 agent-plugins 摄入语义对齐，消除"强制 ZIP"。
- **导出（新能力）**：`hecate plugin export` + 两阶段 REST API（preview → execute）：workspace 内 `source ∈ {user, project}` 的 skills 打包为合规 Agent Plugins bundle（多 skill 单 bundle）；git-ready 目录为主产物、ZIP 为下载 transport；名称净化 + 冲突消解映射记录于 `hecate.*` frontmatter metadata；快照语义——导出物是静态拷贝，不回连平台、不激活不执行。
- **Scanner 扩展**：namespace 文件角色进 file-role × severity 矩阵；namespace 清单 `permissions` 条目纳入既有内容审计规则。
- **Namespace 治理**：单一常量 `AGENT_PLUGIN_NAMESPACE = "io.github.xueyufish"` 落在新模块 `core/plugin/dual_format.py`，不做配置（格式身份 ≠ 部署策略；规范要求全生态唯一且稳定）；通过 CLI/API 输出、docs/gotchas.md、guard test 三重渠道保证"想不起来"时找得到。目录占位符 `io.hecate`（feature-catalog 待确认项）就此关闭——该域名已被他人注册使用，`io.github.xueyufish` 零成本且与 pyproject URLs 一致。

**非目标**：第三方命名空间的语义化（读侧仅识别自家一个，其余按规范忽略）；registry/市场分发与签名信任链（pin-by-hash 已覆盖 v1）；导出 MCP servers 或 `source ∈ {system, plugin, learned}` 的 skills；移除 legacy bundle 支持；SkillModel 数据模型变更。

## Capabilities

### New Capabilities

- `plugin-export`: workspace skills → Agent Plugins bundle 导出——两阶段 API、来源过滤、名称净化与冲突消解、快照语义、CLI 与 REST 面、资源预算。

### Modified Capabilities

- `agent-plugins-ingestion`: 新增两条需求——namespace 扩展识别（extensions 字段 + 同名目录解析、plugin.json 身份唯一、未知命名空间忽略）与双格式代码组件（entry 载荷、tier 调度扩展、enable/startup 注册）。
- `plugin-packaging`: 「Plugin bundle format」改为双布局（legacy + dual-format）；新增「directory/git 安装源（ZIP 降为 transport-only）」与「双格式打包输出」需求。
- `plugin-content-scanning`: file-role 矩阵新增 namespace 角色；namespace 清单 permissions 纳入审计范围。

## Impact

- **core/plugin**：新模块 `dual_format.py`（namespace 常量 + 私有清单解析/生成）；`agent_plugins.py`（namespace 识别——`ALLOWED_MANIFEST_FIELDS` 已含 `extensions`，本次接上语义）；`packaging.py`（双格式打包）；`installer.py`（dir/git 源 + 双布局检测）；`loader.py`（namespace 载荷的 entry 模块解析）；`content_scanner.py`（+1 文件角色、permissions 审计接线）。
- **studio/plugin**：`service.py`（namespace 分支、组件 tier 判定、enable 时代码组件注册）；新增 export service。
- **CLI**：`cli.py` 新增 `export` 子命令；`install` 支持 `--source dir|git`。
- **API**：`POST /api/plugins/export/preview`、`POST /api/plugins/export`（ZIP 下载）。
- **配置**：无新必配项——namespace 是代码常量非配置；导出预算复用既有 size caps。
- **测试**：dual_format 单测、摄入 namespace 场景、打包/安装双布局检测、导出端到端、scanner 新角色、namespace 常量 guard test。
- **文档**：docs/gotchas.md 新增 namespace 条目；roadmap/feature-catalog 5.5d 状态与占位符决议更新（archive 阶段）。
