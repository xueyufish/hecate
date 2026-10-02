# runtime-standalone-distribution Specification

## Purpose

定义 Hecate Runtime 内核独立发行包(`hecate-runtime`)的行为契约:业务方不安装完整 Hecate 主应用即可获得可执行的内核,可选能力缺失在启动期显式声明,平台兼容导入单向且薄,使独立执行宿主(step5c)与后续独立 profile 认证有可安装的基座。

## Requirements

### Requirement: Independently installable runtime package

`hecate-runtime` MUST 作为独立 Python 发行包发布:其安装闭包 MUST NOT 包含完整 `hecate` 主应用(反向依赖禁止),MUST NOT 把平台域(tools/enterprise/channel/studio/ops 的实现)或平台配置(`hecate.core`)带入内核 import 面。包 MUST 能以非 editable 方式构建并在干净虚拟环境中安装成功;安装后不挂载仓库源码路径的条件下,内核 MUST 能完成一次 stub 模型的确定性执行冒烟(编译图、运行至完成、产生事件)。主应用对内核的兼容导入(`hecate.runtime.*`)MUST 仅作转发,不包含实现逻辑;主应用依赖声明 MUST 增加对 `hecate-runtime` 的依赖。

#### Scenario: Clean install supports a stub execution

- **WHEN** 在新虚拟环境中安装 `hecate-runtime` 的非 editable wheel(不安装完整 Hecate、不设置源码 PYTHONPATH)
- **THEN** 内核导入成功并以 stub 模型完成一次确定性执行,事件可读取

#### Scenario: No reverse dependency on the full application

- **WHEN** 检查包的依赖声明与内核源码 import 面
- **THEN** 不存在对 `hecate`(主应用)的依赖或对 `hecate.core`、平台域实现模块的 import

### Requirement: Optional capabilities are declared unavailable at startup

依赖可选发行物(extras)或平台注入点的内核能力(如 Memory 整合、Sandbox 卸载、A2A 交接),在对应组件未安装或未注入时 MUST 在装配/启动阶段以显式的能力不可用声明表达,MUST NOT 以运行中 ImportError 暴露;已启用能力的组合 MUST 反映在能力声明中。装配入口对每个可选能力 MUST 提供启用检查,未启用路径不触发对应 import。

#### Scenario: Missing optional extra surfaces unsupported, not a crash

- **WHEN** 未安装 Memory 相关可选依赖即启动内核装配
- **THEN** 装配完成且对应能力声明为不可用;调用该能力的路径得到显式 unsupported 结果,进程不因 ImportError 崩溃

#### Scenario: Installing the optional extra enables the capability

- **WHEN** 安装对应可选依赖后重新装配
- **THEN** 能力声明翻转为可用,功能路径正常执行

### Requirement: Kernel decoupled from platform configuration

内核代码 MUST NOT import 平台配置单例、平台数据库或平台组合根(`hecate.core` 的 settings、数据库工厂、composition);内核运行所需配置 MUST 由内核自有的最小配置对象承载,由平台或宿主在装配时注入或以默认值构造。内核行为在注入相同配置时 MUST 与抽取前一致。

#### Scenario: Kernel import scan finds no platform configuration

- **WHEN** 对独立包源码执行 import 扫描
- **THEN** 不存在对平台配置、数据库或组合根模块的引用;配置经注入对象传入

#### Scenario: Injected configuration preserves behavior

- **WHEN** 以与抽取前等价的配置注入装配并运行既有内核测试
- **THEN** 测试断言不因抽取与注入改造而变化

### Requirement: Platform compatibility imports are thin and one-way

主应用侧的兼容层 MUST 只做向新包的 re-export 与转发,不含实现、不新增行为;经兼容层访问内核的既有调用方与测试 MUST 不修改 import 即保持行为;兼容层 MUST 有登记的退出条件,其存在 MUST NOT 成为内核反向依赖主应用的理由。

#### Scenario: Existing callers work through the shim unchanged

- **WHEN** 既有测试与平台调用方经 `hecate.runtime.*` 兼容层访问内核
- **THEN** 行为与抽取前一致,无需修改调用方 import

#### Scenario: Shim carries no implementation

- **WHEN** 检查兼容层源码
- **THEN** 仅含转发与 re-export,不含业务或装配逻辑;内核包内不存在对主应用模块的 import
