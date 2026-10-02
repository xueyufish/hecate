# Proposal

## Why

step5a 已交付共享装配(`runtime/execution_assembly.py`,仅依赖 `hecate.runtime.*`)与 builtin 执行后端(`execution/builtin.py`,平台 ORM 查询留在平台 adapter),但内核仍在主应用包内:安装 `hecate` 即安装全部平台依赖,`pyproject.toml` 无独立 Runtime 发行包,`test_runtime_self_sufficiency.py` 的导入隔离证明不能替代干净安装验证(独立基线 §2/§3 结论)。方案 step5b 要求按依赖闭包抽取 `hecate-runtime` 独立发行包并清除首个独立 profile 路径上的延迟跨域 import;这是 I-Ba(独立执行技术预览)的前半段交付,wheel 干净安装是后续宿主(step5c)与 SC 场景翻转的前提。

## What Changes

- 新建 workspace 包 `packages/hecate-runtime`,import root `hecate_runtime`,迁移 runtime 内核(93 个模块中除平台侧子包外的全部:compiler/pregel/workers/context/checkpoint/eventstore/evidence/security/execution_assembly 等)。
- **打破 packages/* 反向依赖惯例**:既有 workspace 包均依赖 `hecate>=0.1.0`,`hecate-runtime` MUST NOT 依赖完整 `hecate`;方向反转为主应用依赖 `hecate-runtime`。
- `src/hecate/runtime/` 变为薄兼容转发层(只 re-export 到 `hecate_runtime`,不含实现),既有测试与调用方不改行为;平台侧模块 `runtime/api/hooks.py` 与 `runtime/temporal/` 不进独立包,迁至平台侧归属(hooks 为配置面,temporal 为平台执行接入)。
- 解除内核对 `hecate.core` 的耦合(独立基线 §2.①"待解除耦合"):5 处模块级 import(compaction/encryption/llm_guard 的 `settings`、temporal 的 `Settings`、api 的 `get_db`)及 `context_processors`/`task_memory_hook` 的函数级 `core.config`/`core.database`/`core.composition` 引用,改为内核自有配置面或装配注入。
- 处置首个独立 profile 路径上的懒加载行(runtime/AGENTS.md 清单):`tool_access`→shell_analysis、`tool_worker`→builtin 名称集、`coordinator_worker`→studio templates、`task_memory_hook` 的 ORM import 改经 Provider Protocol(其 AGENTS.md 退出条件);其余可选行(memory/sandbox/a2a)以 extras 或注入点声明,未安装时**启动即声明 `unsupported`**,不产生运行中 ImportError。
- 构建 非 editable wheel;CI 新增干净安装 job(新 venv、不挂源码路径、安装 wheel + stub 执行冒烟);`test_runtime_self_sufficiency.py` 升级为包级探测(直接探测 `hecate_runtime`,blocked 前缀扩展到全部 `hecate.*`)。
- SC 场景不翻转:SC01/SC02(冷启动+只读执行)需宿主交付,留给 step5c;本 change 的验收是 wheel 级干净安装与依赖闭包成立(方案 5b 验收原文),不以安装冒烟冒充冷启动。

## Capabilities

### New Capabilities

- `runtime-standalone-distribution`:Runtime 内核独立发行包的行为契约——可独立安装性(最小依赖闭包、无反向依赖)、可选能力启动期显式不可用声明、内核与平台配置解耦、兼容转发层的单向薄性。

### Modified Capabilities

(无——`pregel-runtime` 描述引擎行为、`runtime-pluggability` 描述扩展点准入,本 change 不改其需求;契约类型随包发布不改 `execution` 契约语义。)

## Impact

- **新增**:`packages/hecate-runtime/`(pyproject、src/hecate_runtime/、README);CI 干净安装 job。
- **修改**:`src/hecate/runtime/` 变 shim(平台侧 `api`/`temporal` 迁出);根 `pyproject.toml`(workspace members、主应用依赖加 hecate-runtime);`tests/test_runtime/test_runtime_self_sufficiency.py`(包级探测);`src/hecate/runtime/AGENTS.md`(懒加载清单随处置更新、迁移指引);`main.py` 挂载路径。
- **CI**:workspace sync 列表加 hecate-runtime;新 job 构建非 editable wheel 并在干净 venv 安装冒烟(不依赖仓库源码路径)。
- **不受影响**:引擎行为零变化(既有 `tests/test_runtime/`、`tests/test_services/test_workflow/` 断言不改);API 契约、数据库 schema、`contracts/` 语义。
- **后续衔接**:本包是 step5c 宿主(`hecate-runner`)的直接依赖;shim 层退出条件归 step19。
