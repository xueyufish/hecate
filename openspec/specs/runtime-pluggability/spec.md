# Runtime Pluggability Specification

## Purpose

runtime 包的业务自足与可插拔语义：runtime 内（含函数级懒导入）不存在业务模块依赖，具体适配器住在组合根；基础设施实现可替换而 Worker 零修改（契约测试为证）；允许的跨域依赖以带退出条件的清单维护，扩展点无第二实现或明确消费者不新增。

## Requirements

### Requirement: runtime 业务自足

`hecate.runtime` 包 SHALL NOT 依赖业务模块（`hecate.models`、`hecate.studio`、`hecate.channel`、`hecate.tools`、`hecate.ops`、`hecate.enterprise`）——**包括函数内、条件内与 try 块内的懒导入**。RuntimePort 的具体适配器 SHALL 住在组合根（`core/composition`）。运行时自足探针 SHALL 以 AST 全量扫描执法（覆盖所有导入位置），违规即测试失败。

#### Scenario: 适配器迁出后 runtime 无业务依赖

- **WHEN** 对 `hecate/runtime` 全目录（含所有 .py 文件的全部导入语句位置）执行业务前缀扫描
- **THEN** 无任何业务模块导入命中

#### Scenario: 懒导入逃逸被探针捕获

- **WHEN** runtime 内某文件在函数体内新增 `from hecate.models...` 导入
- **THEN** 自足探针测试失败（此前 AST 扫描放行此类导入）

### Requirement: 基础设施可替换且 Worker 零修改

事件存储、记忆提供者、LLM 调用端口的基础设施 SHALL 各有至少两个可实例化的实现（生产实现 + 契约替身），且 SHALL 由同一组契约测试参数化验证——契约通过即保证任何满足契约的实现可在不修改任何 Worker 的情况下替换现有实现。

#### Scenario: EventStore 替换不触碰 Worker

- **WHEN** 契约测试分别以生产实现与契约替身运行同一组事件存储用例（追加、读取、回执查询），并将替身注入 ToolWorker 执行工具
- **THEN** 两组用例全部通过，且测试过程中 Worker 代码零修改

#### Scenario: 记忆提供者契约替身满足同一契约

- **WHEN** 记忆提供者的契约用例分别对生产实现与契约替身运行
- **THEN** 检索/生命周期语义在两实现上一致

### Requirement: 跨域依赖清单带退出条件

runtime 允许的跨域依赖 SHALL 以清单维护（`runtime/AGENTS.md`），每条 SHALL 记录：依赖对象、存在原因、退出条件（何种条件下以何种方式移除）。新增跨域依赖 SHALL 先入清单再引入；扩展点（ABC/Protocol）在无第二实现且无明确消费者时 SHALL NOT 新增。

#### Scenario: 清单条目含退出条件

- **WHEN** 检查 runtime 跨域依赖清单的任一条目
- **THEN** 该条目包含退出条件描述

#### Scenario: 无消费者的扩展点不新增

- **WHEN** 提议新增一个运行时扩展点（ABC/Protocol）
- **THEN** 必须同时给出第二实现计划或明确消费者，否则不引入

### Requirement: 共享执行服务面向执行驱动契约且不依赖具体图执行类

共享执行应用服务（`hecate_runtime` 的 execution service）SHALL 面向 Runtime 内部执行驱动契约工作:契约只包含服务实际消费的执行面——按会话发起执行、按声明的模式与输入产生事件流、接受不透明恢复负载、事件流可提前关闭;恢复负载的语义由具体驱动自行解释。服务 SHALL NOT 导入或判断任何具体图执行类（含 `PregelRuntime`）,不把图模型语义（Graph、Channel、超步、checkpoint 格式）纳入契约。该契约服务于内置执行 Controller 与 Workflow/Pregel 驱动,SHALL NOT 扩展为外部 Runtime 插件或通用执行框架;外部 Runtime 接入不在范围内。

#### Scenario: 阻断图执行类导入后共享服务仍可加载并驱动替身

- **WHEN** 测试环境阻断 `hecate_runtime.pregel` 的模块导入（自足探针同款机制）,随后加载共享执行服务并以非图驱动替身执行一次完整事件流
- **THEN** 服务加载成功,事件捕获与结果分类正常完成,全流程不触发对图执行类的任何导入

#### Scenario: 契约不含图私有语义

- **WHEN** 审阅执行驱动契约的公开面（方法签名、参数与事件类型）
- **THEN** 契约仅包含会话、输入、执行/流模式、不透明恢复负载与事件流,不含 Graph/Channel/checkpoint 等图私有类型

### Requirement: 执行驱动有生产实现与契约替身并由消费者经边界使用

执行驱动契约 SHALL 有至少两个可实例化的实现:生产 Pregel 驱动（包装既有图执行,转发参数与事件,不修改图执行类本身）与契约测试替身（非图实现）,并由同一组契约测试参数化验证——事件转发、提前关闭与恢复负载透传语义一致。平台的 builtin 执行后端与独立 Runner 引擎 SHALL 经该驱动边界传入执行实现,SHALL NOT 绕过边界直接向共享执行服务传递具体图执行实例。

#### Scenario: 契约测试参数化覆盖两种驱动

- **WHEN** 同一组契约用例（事件逐个转发、调用方可提前关闭流、恢复负载原样透传）分别以 Pregel 生产驱动与契约替身运行
- **THEN** 两组用例全部通过,且共享执行服务代码零修改

#### Scenario: 真实消费者经驱动边界执行

- **WHEN** 平台 builtin 执行后端与 Runner 引擎分别发起一次真实图执行
- **THEN** 二者传入共享执行服务的是执行驱动实现,既有事件、审批等待与恢复行为与本变更前一致

### Requirement: 共享服务的执行语义在解绑后保持不变

解绑 SHALL NOT 改变共享执行服务的既有行为:事件观察决策（继续/未知即停）、协作取消检查、未知即停时的事件流提前关闭、调度取消包装,以及成功/失败/取消/未知四类结果分类 SHALL 与本变更前逐项一致。提前停止路径 SHALL 完成事件流清理,不遗留未终止的后台执行任务。

#### Scenario: 分类与取消语义回归通过

- **WHEN** 以生产 Pregel 驱动重放既有共享服务行为用例（正常完成、驱动异常、观察者要求未知即停、协作取消、外部调度取消）
- **THEN** 五类场景的结果状态、事件序列与错误详情分类均与本变更前一致

#### Scenario: 提前停止不留后台执行

- **WHEN** 观察者或取消检查触发提前停止,共享服务立即关闭事件流
- **THEN** 流关闭在当前执行上下文完成,不遗留运行中的后台执行任务
