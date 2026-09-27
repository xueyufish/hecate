# runtime-pluggability Delta

## Purpose

runtime 包的业务自足与可插拔语义：runtime 内（含函数级懒导入）不存在业务模块依赖，具体适配器住在组合根；基础设施实现可替换而 Worker 零修改（契约测试为证）；允许的跨域依赖以带退出条件的清单维护，扩展点无第二实现或明确消费者不新增。

## ADDED Requirements

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
