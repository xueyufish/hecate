# Tasks

## 1. Namespace 基座

- [x] 1.1 新建 `src/hecate/core/plugin/dual_format.py`：`AGENT_PLUGIN_NAMESPACE = "io.github.xueyufish"` 常量、`NAMESPACE_DIR_NAME`、namespace 清单内容模型（允许字段集：type/entry/permissions/config_schema/metadata）与 `parse_namespace_manifest(path) -> PluginManifest 兼容结构`；畸形清单报 `AgentPluginValidationError` 子类。验证：单测覆盖合法/畸形/缺省清单解析
- [x] 1.2 Guard test：断言常量值与模块路径（`tests/test_core/plugin/test_dual_format.py`），确保 namespace 变更必须在测试层显式发生。验证：`python -m pytest tests/test_core/plugin/test_dual_format.py -v` 通过
- [x] 1.3 CLI 可见性：`hecate plugin --help` 与 `agent-install` 成功输出附带 namespace 值；`docs/gotchas.md` 新增 namespace 条目（值、位置、语义、改动需过 guard test）。验证：`hecate plugin agent-install --help` 可见；gotchas 条目存在

## 2. 读侧收敛（ingestion）

- [x] 2.1 `agent_plugins.py`：plugin.json 校验通过后接 namespace 识别——读 `extensions[AGENT_PLUGIN_NAMESPACE]`（非对象 → warning）、定位同名顶层目录、`parse_namespace_manifest` 解析；name/version 与 plugin.json 冲突 → 拒绝，畸形清单 → 降级 warning 按普通 agent-plugin 装。验证：单测覆盖识别/冲突拒绝/降级/其他 namespace 忽略四场景
- [x] 2.2 `studio/plugin/service.py`：`install_agent_plugin` namespace 分支——manifest_ 携带 namespace 清单与组件 tier 判定；code entry 组件按 D5 skip-with-warning（非平台安装者/T0 拒绝），组件清单标记 `denied`；"一行"不变式与既有 reinstall/collision 语义不变。验证：服务层单测覆盖平台/工作区安装者 × 有/无 code entry 矩阵
- [x] 2.3 端到端摄入测试：构造双格式包 fixture（plugin.json + namespace/plugin.yaml + skills/），`install_agent_plugin` 后断言单行、清单内容、组件清单。验证：`python -m pytest tests/test_studio/plugin/ -v` 通过

## 3. Loader 与 enable 投影

- [x] 3.1 `loader.py`：entry 解析支持 namespace 模块路径——当安装根含 namespace 目录时将其加入模块搜索路径（`load_plugin` 增加 namespace-aware 入口，T0 `check_python_entry` 前置不变）。验证：单测用临时包目录断言模块可解析
- [x] 3.2 `discover_plugins` 回归：确认根无 plugin.yaml 的双格式包不被根发现拾取（防双载）。验证：回归测试断言双格式目录不被 `discover_plugins` 返回
- [x] 3.3 `service.py` enable/disable/startup replay：enabled 双格式包的 code entry 组件经 loader 注册/反注册/重放（复用既有 MCP replay 钩子位置）。验证：enable → 注册表可见；disable → 不可见；重放测试覆盖重启场景

## 4. Scanner 扩展

- [x] 4.1 `content_scanner.py`：`classify_file` 增加 namespace 分支（`namespace-manifest` 高、`namespace-code` 中），档位进 `ROLE_SEVERITY_CAP`。验证：单测断言同 finding 在 manifest 与 code 文件的 severity 排序
- [x] 4.2 permissions 审计：namespace 清单 permissions 逐条过既有 allowed-tools 规则集，findings 归 `namespace-manifest` 角色；畸形 permissions 值 → finding。验证：单测覆盖危险条目 finding 与畸形值 finding；install 阻断路径沿用 fail-closed 既有测试模式

## 5. 写侧收敛（packaging / installer）

- [x] 5.1 `packaging.py`：新增 `emit_dual_format(plugin_dir, output) -> Path`——生成 plugin.json（name/version/description 直映射，author/homepage 可选）、plugin.yaml 去 name/version 写入 namespace 目录、Python 载荷移入 namespace 目录、skills/mcp.json 透传；open face 为空也输出。验证：单测断言输出树结构 + 解压后 `validate_plugin_json` 通过
- [x] 5.2 `cli.py` `package` 命令切换为双格式输出（legacy 目录检测到根 plugin.yaml 时提示新行为），`--output xxx.hecate-plugin` 产 ZIP transport。验证：CLI 集成测试——package 后 install 回来组件完好
- [x] 5.3 `installer.py`/`cli.py`：`install` 增加 `--source dir|git|zip`，复用 5.5c 物化（dir/git provenance 记录），ZIP staging 解压；布局检测路由——根 plugin.json → agent-plugin 管线，根 plugin.yaml → legacy 路径。验证：三种源 × 两种布局的路由单测；legacy bundle 安装回归通过

## 6. 导出（plugin-export）

- [x] 6.1 导出服务模块（`studio/plugin/export_service.py`）：`preview_export`（计划：bundle 名、净化映射、warnings、尺寸，无副作用）与 `execute_export`（按计划物化）；来源过滤 `source ∈ {user, project}` + workspace 内 + 读权限门禁；`skills/` 布局 + supporting files 拷贝。验证：服务层单测覆盖来源排除、计划一致性
- [x] 6.2 名称净化与冲突：净化到规范语法、frontmatter name 重写与目录一致、确定性数字后缀消歧、`hecate.original-name`/`hecate.workspace`/`hecate.exported-at` 写入 frontmatter metadata。验证：单测覆盖大写/下划线净化、碰撞后缀、metadata 回读
- [x] 6.3 预算：复用 `check_size_caps`，preview 阶段报超限（measured/allowed）。验证：单测构造超限选择断言拒绝
- [x] 6.4 CLI：`hecate plugin export [--workspace] [--skill ...] [--output] [--zip]`——默认 git-ready 目录，`--zip` 产 ZIP。验证：CLI 集成测试产出目录树 + zip 可解压
- [x] 6.5 REST API：`POST /api/skills/export/preview` 与 `POST /api/skills/export`（ZIP 下载），鉴权与 workspace 成员检查沿用现有依赖。验证：API 测试——preview 无副作用、execute 返回 zip
- [x] 6.6 Round-trip 端到端：导出 bundle → `install_agent_plugin` 回装 → skills 无损导入、名字与净化映射一致。验证：端到端测试通过

## 7. 全量验证

- [x] 7.1 四套检查全绿：`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/ -q`。验证：全部 0 error
- [x] 7.2 场景回归：`agent-plugins-ingestion`、`plugin-packaging`、`plugin-content-scanning` 既有测试全通过（零破坏确认）。验证：`python -m pytest tests/test_core/plugin/ tests/test_studio/plugin/ -q` 全绿
