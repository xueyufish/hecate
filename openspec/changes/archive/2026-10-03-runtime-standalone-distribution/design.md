# Design

## Context

- step5a 后的形态:`runtime/execution_assembly.py` 只 import `hecate.runtime.*`(内核纯净);`execution/builtin.py` 只 import contracts + `execution.backend` + 装配(平台 ORM 查询未进内核);`tests/test_execution/test_builtin_backend.py` 钉住 builtin 契约样本。
- 内核现状规模:93 个模块;对 `hecate.core` 的耦合面已量化——模块级 5 处(`compaction.py:52`、`security/encryption.py:7`、`security/llm_guard.py:15` 的 `settings`;`temporal/run_worker.py:14` 的 `Settings`;`api/hooks.py:12` 的 `get_db`),函数级集中在 `context_processors.py`(9 处)、`task_memory_hook.py`(5 处)、`compaction.py`(2 处)。
- 平台侧模块:`runtime/api/hooks.py` 仅被 `main.py` 挂载(原基线 N5 已登记为配置面);`runtime/temporal/` 由 `main.py` 装配、`pregel.py` 有一处引用(实施时核对是否函数级)。两者不属内核。
- 打包惯例:`packages/*` 现有包**全部反向依赖 `hecate>=0.1.0`**(如 hecate-sandbox),import root 均为 `hecate_<name>`;uv workspace `members = ["packages/*", ...]`,CI 以 `uv sync --package <列表>` 安装。
- 自足性探针:`test_runtime_self_sufficiency.py` 在 subprocess 屏蔽域前缀 + workspace wheel 前缀;`runtime/AGENTS.md` 维护 11 行函数级懒加载清单(每行带退出条件),探针对清单外懒加载失败。

## Goals / Non-Goals

**Goals**

- `packages/hecate-runtime` 独立发行:非 editable wheel 可装、依赖闭包最小、干净 venv 中 stub 执行冒烟通过。
- 主应用行为零变化:既有测试经兼容层不改断言全绿。
- 首个独立 profile 路径上的跨域依赖清除 + 可选能力启动期 unsupported 声明。

**Non-Goals**

- 不交付独立宿主(本地 manifest 加载、HTTP 入口、冷启动)——step5c。
- 不翻转 SC01/SC02(需宿主);不做平台入口迁移(step5d)。
- 不收敛主应用对 hecate-ops/llm/sandbox 的默认依赖(方案归 step19);不改引擎行为与 `contracts/` 语义。
- 不为 A2A 交接提供 extras(其实现位于主应用 `channel/`,独立 profile 下该能力声明 unsupported,受管/平台侧经注入点提供)。

## Decisions

### D1: import root 取 `hecate_runtime`,主应用侧留薄 shim

沿用仓库既有模式(`hecate_memory`/`hecate_ops`/`hecate_sandbox` 均为独立 import root)。备选"两个发行版共享 `hecate.runtime` 命名空间"被否决:普通包同名安装互相覆盖,冲突不可控。方向:新包不含任何 `hecate.*` import;`src/hecate/runtime/__init__.py`(及子模块)改为纯 re-export;根 pyproject 主应用依赖加 `hecate-runtime`。**打破既有 packages 反向依赖 `hecate` 的惯例**——这是本包与其他 workspace 包的本质差异,README 与 AGENTS.md 显式登记。

### D2: 包范围 = 内核全量,排除两个平台侧子包

`packages/hecate-runtime/src/hecate_runtime/` ← `src/hecate/runtime/` 全部,**除外**:`api/hooks.py` 迁至 `src/hecate/ops/api/`(原基线 N5 已定性为配置面 CRUD,挂载路径改 `main.py`);`temporal/` 迁至 `src/hecate/execution/temporal/`(平台执行接入,归 execution 域;`pregel.py` 对它的引用改为注入点或函数级可选——实施时按实际形态选择,若为模块级则必须改注入)。迁移用脚本做机械 import 重写(`hecate.runtime.X` → `hecate_runtime.X`),测试同步替换后全量回归。

### D3: core 解耦策略——内核自有最小配置对象 + 装配注入

新建 `hecate_runtime/config.py`:`RuntimeConfig` dataclass 承载内核实际消费的配置字段(从 5 处模块级引用与 `context_processors`/`compaction` 的函数级引用中枚举:压缩阈值、加密密钥引用、llm guard 开关、记忆策略参数等——字段清单在实施 PR 中以 grep 证据固定)。规则:内核模块**只 import RuntimeConfig**,默认值构造保证独立可跑;`execution_assembly.assemble_execution` 增加可选 `config: RuntimeConfig | None` 参数,平台/宿主注入;主应用装配处从 `settings` 映射构造 RuntimeConfig。`core.database`/`core.composition.memory_provider` 的函数级引用改为注入点(见 D4 的 task_memory_hook 行)。**禁止**为省事把 `core/config.py` 整个搬进内核。

### D4: 懒加载清单按"在独立 profile 路径上"分两档处置

- **清除/改造(本 change)**:`tool_access`→`tools.tool.shell_analysis`(改为注入的 shell 分析接口,默认 no-op 策略对象);`tool_worker`→`tools.tool.builtin` 名称集(名称集移入内核常量或注入);`coordinator_worker`→`studio.workflows.templates`(模板构造参数化,动态编排能力在独立包声明为可选注入);`task_memory_hook` 的 `hecate_memory` + `EpisodeModel` ORM import(按其 AGENTS.md 退出条件:写回经 Memory Provider Protocol,删除 ORM import);`security/*` 对 `ops.dlp`/`findings_writer` 的引用确认全部经 DI(已登记为宿主可注入,核对无直接函数级残留)。
- **保留为可选声明(不清除)**:`compaction`/`context_processors`→`hecate_memory.consolidation`(extras: `memory` → 依赖 `hecate-memory`);`offloader`→`hecate_sandbox`(extras: `sandbox` → 依赖 `hecate-sandbox`);`agent_tool`→`channel.a2a.client`(独立包无 extras,声明 unsupported,注入点供平台/宿主)。
- 装配入口提供能力探测(`capability_status()`:installed/unsupported),未安装 extras 时启动声明 unsupported;探针的 sanctioned 清单同步更新到新包路径,`runtime/AGENTS.md` 清单逐行标注处置结果与剩余退出条件。

### D5: 验证体系三层

1. **既有探针改造**:保留 `hecate.runtime` shim 路径探测(主应用行为);新增 `hecate_runtime` 直接探测,blocked 前缀 = 全部 `hecate.*` + 按需 wheel 前缀——shim 的转发目标在探测中显式放行 `hecate_runtime`。
2. **包级测试迁移**:`tests/test_runtime/` 中纯内核测试随代码迁移改为 import `hecate_runtime`(或经 shim——按"测试测什么"决定:内核行为测试直接用新 root,平台集成测试留 shim 路径)。
3. **CI 干净安装 job**(独立于现有 matrix):`uv build --package hecate-runtime` → 新建 venv → `pip install dist/*.whl` → 运行随包分发的冒烟脚本(stub 模型执行一次图,断言事件序列)——脚本放 `packages/hecate-runtime/smoke/` 随仓库分发但不入 wheel 或随 wheel 分发二选一,倾向**随仓库分发**(CI 用,包消费者用文档示例),避免 wheel 内塞测试代码。

### D6: PR 分四批,每批可独立回退

PR1 包骨架+代码迁移+shim+主应用依赖(行为零变化,全量测试绿);PR2 core 解楚(RuntimeConfig 注入,5+16 处引用改造);PR3 懒加载行处置+extras+unsupported 声明+探针/AGENTS.md 更新;PR4 CI 干净安装 job+README/文档。PR1 是纯机械迁移(最大、风险最低),PR2/PR3 逐项带测试。

## Risks / Trade-offs

- [93 模块 import 重写的机械错误] → 脚本替换 + `grep -c "hecate\.runtime"` 归零断言 + 全量 pytest;testmon 辅助内环。
- [`pregel.py` 对 temporal 的引用若为模块级会制造内核→平台依赖] → PR1 前置核查该引用形态;若模块级,PR1 内一并改为注入(阻塞项,不允许带病迁移)。
- [RuntimeConfig 字段遗漏导致行为漂移] → 字段清单以 grep 证据固定;对 settings 默认值做快照对照测试(注入等价配置后既有断言不变,spec 场景已钉)。
- [shim 层被当实现垃圾桶] → 分层测试增加 shim 纯度检查(只允许 import/re-export 行);AGENTS.md 登记 shim 退出条件(step19)。
- [CI 时长增加] → 干净安装 job 只跑构建+安装+冒烟(分钟级),不跑全量测试。
- [Windows/uv workspace 构建差异] → 本地先验证 `uv build --package hecate-runtime` 产物可安装;CI 在 ubuntu 上跑(与既有 matrix 一致)。

## Migration Plan

主应用 `pyproject.toml` 依赖加 `hecate-runtime`(workspace 内解析);`src/hecate/runtime/` shim 保留至 step19 退出;平台侧两模块迁移改 `main.py` import 路径。回退:revert 对应 PR 即可,无数据/状态迁移;PR1 之前主应用行为与 main 完全一致,任何一批失败不影响已合入批次的行为。

## Open Questions

(无——包名与目录方向已由 ADR-035 固定;extras 粒度按 D4 定;A2A 不提供 extras 的取舍记录于 Non-Goals,若 step5c 宿主需要 A2A 交接再以具名消费者重开。)
