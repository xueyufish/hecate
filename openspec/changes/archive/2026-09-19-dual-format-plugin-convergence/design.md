# Design

## Context

5.5c 交付的摄入管线（`core/plugin/agent_plugins.py`）已把 Agent Plugins 1.0 的读侧全部落地：`ALLOWED_MANIFEST_FIELDS` 含 `extensions` 但解析后弃置（`agent_plugins.py:160-163` 只 warn 非对象）——这正是本变更的钩子点。5.5b 的 `packaging.py`/`installer.py` 维持 legacy `.hecate-plugin`（ZIP 强制、`plugin.yaml` 在根）。5.13a 的 `content_scanner.py` 有 file-role × severity 矩阵（`classify_file`/`ROLE_SEVERITY_CAP`）与 allowed-tools 审计规则。`plugin.schema.json` 的 `additionalProperties: false` 与正文"unknown fields warn"相互矛盾，决定了私有内容必须走规范 §5.6/§8.2 的 extensions + 同名目录机制，而非顶层字段。

## Goals / Non-Goals

**Goals:**

- 一个包同时是合规 Agent Plugins 插件与 Hecate 深度集成插件；读侧与写侧双向成立
- workspace skills 可导出为生态可用 bundle；导出物可经 5.5c 管线无损回流（round-trip）
- legacy bundle 与既有管线零破坏（additive）
- namespace 单一事实来源，可发现、可守护

**Non-Goals:**

- 第三方命名空间语义化；registry/市场；签名信任链
- SkillModel / PluginModel 表结构变更（manifest_ JSON 内承载 namespace 信息即可）
- 移除 legacy 路径

## Decisions

### D1 — namespace 是代码常量，不是配置

`AGENT_PLUGIN_NAMESPACE = "io.github.xueyufish"` 落在新模块 `core/plugin/dual_format.py`。不做配置，理由：这是**格式身份**而非部署策略——规范要求一个包在全生态携带同一个命名空间；若做成配置，同一 bundle 在不同部署间身份漂移，跨客户端互操作与安装碰撞语义全部失效。私有化部署真正需要配置的是**策略**（允许什么、扫描阈值），那些已有配置面。域名选择：`io.hecate` 已被他人注册并使用（DNS 解析到 GitHub Pages），`io.github.xueyufish` 零成本、归属明确、与 `pyproject.toml` URLs 一致；未来拥有自有域名后可加读侧识别列表（第二个常量），写侧永远只有一个。

**治理（回应"写死在代码深处会想不起来"）**：常量单一来源；`hecate plugin` CLI 帮助文本与 agent-plugin 安装 API 响应携带 namespace 值；`docs/gotchas.md` 增加条目；guard test 断言常量值并在有人改动时失败出声（改动 namespace 本身就该是一次显式的、有迁移语义的决策）。

### D2 — namespace 目录内容模型：声明式清单 + 载荷同目录

`io.github.xueyufish/plugin.yaml`（Hecate 私有清单：type/entry/permissions/config_schema/metadata）+ entry 引用的 Python 载荷，两者都在 namespace 目录内——**清单是声明式的，载荷随私有内容同住 namespace 目录**。理由：

1. §8.2 的扩展目录是规范唯一祝福的私有内容位置；载荷放包根会在 open face 留下规范未定义的顶层目录，恰好破坏"对其他客户端 100% 干净"这一收敛目标。
2. 私有内容（清单+载荷）单子树聚集 → scanner 角色、路径遏制（`resolve_contained` 已覆盖）、uninstall 级联全部沿用现机制，零新增攻击面分类。
3. 信任模型不变：载荷仍是代码，照旧过 T0 闸门——位置不改变信任，闸门才改变信任。

替代方案（载荷在包根、模块目录命名随插件）被否：省了 loader 一处改动，代价是 open face 纯净性这个本特性的核心卖点。

entry 语法不变（`python:<module>:<Class>`）；loader 在解析时把 `<install_root>/io.github.xueyufish` 加入模块搜索路径。载荷新约定：模块目录以插件名开头（沿用 init 模板惯例），缓解跨包同名模块碰撞（与 legacy `plugins/<name>/` 布局同级风险，不恶化）。

### D3 — replace-not-merge，plugin.json 唯一身份

namespace plugin.yaml 中 name/version 与 plugin.json 冲突 → 拒绝安装；缺省或一致 → 通过。打包工具产出的 plugin.yaml 干脆不含 name/version。理由：两个身份来源必然漂移；plugin.json 是所有客户端共享的唯一公约数。合并语义被否：错误信息无法归因，且一个包在不同客户端看到的名字可能不同——直接违背收敛目标。

### D4 — 双格式包落 agent-plugin 行，legacy 路径不交叉

双格式包（根有 plugin.json）一律走 5.5c 管线 → `type="agent-plugin"` 单行，namespace 信息（清单内容、组件 tier 判定）进 `manifest_` JSON。legacy bundle（根有 plugin.yaml）继续走 legacy installer → `type="plugin"` 行。布局检测在 materialize 之后做（根上有什么清单就是什么），CLI 层路由。**不做**"legacy 布局也迁 agent-plugin 行"——那是数据迁移，不是本特性；两条路径各自的行类型、信任模型、生命周期已稳定。

由此 loader 的根目录发现（`discover_plugins` 只认根 `plugin.yaml`）天然不会双载双格式包——enable 投影是唯一加载通道（见 D6）。

### D5 — 包安装权限 = 最高组件 tier；非授权组件 skip-with-warning

组件 tier：code entry → T0（`check_python_entry`，平台安装者 + allowlist 语义）；stdio → T1；http-MCP → T2；skills → T4。请求者不满足最高 tier 时，仅该组件 skip 并记录 denial（组件清单标记），声明性组件照常安装——完全镜像 5.5c 的 stdio 先例，不发明新语义。T0 拒绝的具体判定复用 `PythonEntryPolicy`（SaaS 拒绝 / self-hosted 默认拒绝 + `PLUGIN_PYTHON_ENTRY_ALLOWLIST`），fail-closed。

### D6 — enable 投影承载代码组件

双格式包 enable 时：T0 检查通过 → `load_plugin` 经 loader 加载（namespace 目录入模块路径）→ 注册进 PluginRegistry；disable 反注册；startup replay 在既有"MCP 重放"处并列"代码组件重放"。触摸点集中在 `studio/plugin/service.py` 的 enable/disable 与 composition 根的 replay 钩子。legacy 的 `discover_plugins` 启动扫描不感知双格式包（D4），两条加载通道互不重叠。

### D7 — 打包输出映射与 ZIP 降级

`hecate plugin package <dir>`：

- 读根 `plugin.yaml` → 生成 `plugin.json`（name/version/description 直映射；author/homepage 经 CLI 选项可选携带；`$schema` 指向 1.0.0）
- `plugin.yaml` 去除 name/version 后写入 `io.github.xueyufish/plugin.yaml`；Python 载荷移入 namespace 目录；`skills/`、`mcp.json` 原样透传
- open face 为空也统一输出（规范允许组件缺省；"没有 skills 的合规包"是合法且常见的——纯工具插件正是如此）
- 默认输出 git-ready 目录；`--output xxx.hecate-plugin` 输出 ZIP transport（解压即合规包）

`hecate plugin install` 增加 `--source dir|git|zip`：dir/git 复用 5.5c 物化机制（`materialize_from_dir`/`materialize_from_git`，含 ref/SHA provenance）；ZIP 仅 transport（staging 解压 → 布局检测 → 路由）。legacy ZIP 安装入口原样保留（additive）。

### D8 — Scanner：+1 角色族 + permissions 审计

`classify_file` 增加 namespace 分支：`io.github.xueyufish/plugin.yaml` → `namespace-manifest`（高暴露：其值直接进入权限/配置决策）；namespace 下代码文件 → `namespace-code`（中：T0 闸门之后才执行，扫描是纵深防御）。矩阵具体档位在实现时对齐 `ROLE_SEVERITY_CAP` 既有粒度。permissions 审计：解析 namespace 清单 permissions 列表，逐条过既有 allowed-tools 审计规则集，findings 归 `namespace-manifest` 角色；permissions 值畸形 → finding（不静默跳过）。install 与 enable rescan 两处自动生效（同一 ScanStage 协议）。

### D9 — 导出：两阶段、快照、净化、预算

新 `plugin-export` 能力。服务层 preview/execute 两方法（无副作用 → 有副作用）：

- 来源：`SkillModel.source ∈ {user, project}` 且同 workspace；读权限即导出权限
- bundle：单 `plugin.json` + `skills/<dir>/SKILL.md`（+ supporting files）；名称净化到规范语法，frontmatter `name` 重写为与目录一致，`hecate.original-name`/`hecate.workspace`/`hecate.exported-at` 写入 frontmatter `metadata`（规范合法的自由字段，其他客户端可见但不解析）；碰撞确定性加数字后缀
- 快照语义：导出不激活不执行；产物与源无回连
- 预算：复用 `check_size_caps` 的包级上限与默认值；preview 阶段即报超限
- 面：CLI `hecate plugin export [--workspace] [--skill ...] [--output] [--zip]`（目录为主产物）；REST `POST /api/plugins/export/preview` + `POST /api/plugins/export`（ZIP 下载，挂 plugins 路由——与 CLI 及 install/upload 同面）
- round-trip 是验收标准之一：导出物过 5.5c 摄入应无损回来（净化映射保证名字可预期）

### D10 — 兼容性矩阵（全部 additive）

| 产物 | 旧版 Hecate | 新版 Hecate | 其他客户端 |
|---|---|---|---|
| 旧 legacy bundle | ✅ | ✅（原路径） | ❌（本来就不兼容） |
| 新双格式包 | extensions+目录被忽略，按 open face 装成普通 agent-plugin | ✅ 完整能力 | ✅ 只看 open face |
| 导出 bundle | ✅（5.5c 即可装） | ✅ | ✅ |

无需数据迁移、无默认行为翻转、无配置变更；回滚 = revert 提交，已分发的双格式包在旧版本上自动降级为普通 agent-plugin（前向兼容打包，后向兼容读取）。

## Risks / Trade-offs

- [其他客户端对大体积 namespace 目录的容忍度未知] → 规范明确 MUST ignore 未实现命名空间；实际风险是审查噪音而非功能破坏。缓解：载荷保持最小；skills/mcp.json 始终是互操作面；文档写明"别的客户端看不到你的 Python"。
- [第三方包伪造本 namespace] → 伪造者得到的上限 = 合法提交一个 Hecate 插件：T0 组件要平台安装者 + allowlist，声明性组件各归各 tier，scanner 覆盖 namespace 文件。无需额外防伪机制。
- [双清单认知负担] → replace-not-merge + 冲突即拒绝（D3）+ 打包工具自动生成（作者几乎不手写 plugin.json）。冲突错误信息指明字段与两个值。
- [净化改名导致 round-trip 名字漂移] → 映射在 preview 可见、在 frontmatter metadata 可追溯（`hecate.original-name`）；净化规则确定性可测试。
- [namespace 目录进模块路径引入跨包模块名碰撞] → 与 legacy 布局同级风险（现状已存在）；载荷新约定模块目录带插件名前缀；不引入新机制。
- [导出泄漏敏感内容（skill 正文里写了密钥）] → 导出预览执行与安装同源的 scanner 规则子集（secret 检测类）作为 warning 不阻塞？——**不**，v1 导出不跑 scanner：导出的是用户自己的内容（读写权限已门禁），与"安装第三方内容"信任方向相反；preview 的 warnings 限名称/尺寸类。此取舍记录于此，若后续有合规需求再作为导出侧选项加入。

## Migration Plan

纯 additive：新模块、新子命令、新端点、既有函数加分支。部署顺序无要求；回滚 revert 即可（D10 矩阵保证降级安全）。唯一"一次性"动作：`docs/gotchas.md` 与 roadmap/catalog 的 5.5d 状态更新放在 archive 阶段完成。

## Open Questions

（无——三个设计级选择均已在此定案：多 skill 单 bundle（D9）、open face 为空也统一输出（D7）、`.hecate-plugin` 兼容保留（D10）。）
