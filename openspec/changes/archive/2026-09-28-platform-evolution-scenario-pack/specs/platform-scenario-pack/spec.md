# Spec Delta

## Purpose

为企业 Agent 平台演进提供一个可重复、可追溯的架构评测场景包:以统一的 manifest 关联场景、断言与 P01—P08 能力映射,区分 CI 确定性场景与记录基线,使后续演进步骤能对照同一基线比较行为、权限与副作用结果。

## ADDED Requirements

### Requirement: Scenario manifest is the single source of truth

场景包 MUST 提供一份机器可读的 manifest(建议 `tests/scenarios/manifest.yaml`),每个场景条目 MUST 声明:场景 ID、对应的 P01—P08 问题组、入口类型、断言摘要、支持或未支持的能力。未支持的能力 MUST 显式标注(如 `unsupported` / `deferred` 及原因),不得以缺省或静默跳过表达。`docs/research/platform-evolution-baseline.md` MUST 引用 manifest,不复制其内容。P01—P08 每个问题组 MUST 在 manifest 中出现,或声明覆盖状态为覆盖/部分覆盖/未支持。

#### Scenario: Manifest coverage is complete

- **WHEN** 校验脚本检查 manifest
- **THEN** P01—P08 每个问题组都有条目,且每条目标明覆盖状态;缺少条目或缺少覆盖状态时校验失败

#### Scenario: Unsupported capability is explicit

- **WHEN** 某能力(如图检索,对应 P04)在本轮不验证
- **THEN** manifest 对应条目标注 `deferred` 及原因,且引用该能力的场景不计为覆盖通过

#### Scenario: Baseline doc references manifest

- **WHEN** 基线文档陈述 P01—P08 映射或场景清单
- **THEN** 它引用 manifest 中的场景 ID,manifest 变更时基线文档无需手工同步场景明细

### Requirement: Tier separation between deterministic CI and recorded baselines

场景 MUST 划分为两层:Tier 1(CI 确定性层)只依赖 stub 模型、in-memory SQLite 与 stub 企业工具,随 pytest 在 CI 运行;Tier 2(记录基线层)依赖真实模型或产生非确定性结果,以记录产物(JSON 及说明)形式存在,MUST NOT 在 CI 中执行。权限与副作用断言 MUST 使用确定性断言;内容质量断言 MUST 使用明确 rubric 并记录 evaluator 版本。

#### Scenario: Tier-1 runs hermetically

- **WHEN** 在无网络、无真实模型凭据的环境中运行场景包的 CI 部分
- **THEN** 全部 Tier 1 场景通过,不发起真实外部调用

#### Scenario: Tier-2 stays out of CI

- **WHEN** 查看 CI 测试收集范围
- **THEN** Tier 2 的跑批脚本与记录产物不被 CI 收集或执行

### Requirement: Five repeatable end-to-end scenarios

场景包 MUST 通过真实执行入口提供五个可重复场景,并以业务结果断言(不逐字比较模型文本):正常执行(读取材料并产出预期产物)、拒绝动作(未授权写被拒且有证据)、等待审批(高风险动作进入审批等待,批准后继续)、后端失联(执行后端不可用时状态显式,不虚报成功)、重复提交(同一动作重复提交不产生第二次副作用)。入口按场景语义选择其真实所在层:入口级行为(正常执行、拒绝、审批、后端失联)走真实 HTTP 聊天入口;恢复与幂等语义(重复提交)在引擎 ToolWorker 执行边界上断言——该层是回执与恢复语义的实际所在。每个场景 MUST 在 manifest 中登记入口类型与断言摘要。

#### Scenario: Normal execution produces expected artifact

- **WHEN** Agent 通过入口读取授权材料并执行摘要任务
- **THEN** 任务完成且产物符合预期 schema,断言比较业务结果

#### Scenario: Denied action leaves evidence

- **WHEN** Agent 请求未授权的写操作
- **THEN** 动作被拒绝,拒绝记录可查询

#### Scenario: Approval gates high-risk action

- **WHEN** Agent 触发标记为需审批的动作
- **THEN** 执行等待审批,批准后动作继续,拒绝则不执行

#### Scenario: Backend loss is explicit

- **WHEN** 执行后端在动作后失联或注入故障
- **THEN** 状态显式标记未知/待对账,不报告成功

#### Scenario: Duplicate submit has no second side effect

- **WHEN** 同一动作被重复提交或恢复
- **THEN** 受保护副作用只发生一次,重复请求得到一致响应或冲突拒绝

### Requirement: Permission negative cases across entries

场景包 MUST 提供权限负例:viewer 角色尝试写操作、跨租户访问资源、未经审批的受保护写、MCP 入口与 REST 入口对同一请求返回一致授权结果。每个负例 MUST 断言拒绝结果与可查询证据。

#### Scenario: Viewer cannot write

- **WHEN** viewer 角色通过入口请求写操作
- **THEN** 请求被拒绝且拒绝证据可查

#### Scenario: MCP and REST produce same authorization result

- **WHEN** 同一未授权请求分别经 MCP 入口与 REST 入口提交
- **THEN** 两者的授权结果一致,且均基于服务端可信上下文

#### Scenario: Cross-tenant access is rejected

- **WHEN** 请求访问其他租户的资源
- **THEN** 请求被拒绝

### Requirement: Side-effect recovery negative cases

场景包 MUST 覆盖不确定副作用的恢复语义:已记录调用但结果缺失时,受保护写入 MUST 停止并进入待对账,不得盲目重试;恢复路径 MUST 返回真实结果引用而非占位文本;同一动作键参数变化 MUST 被拒绝。

#### Scenario: Missing result stops protected write

- **WHEN** 注入"动作已执行、结果未落盘"的故障后触发恢复
- **THEN** 系统不重复执行写操作,状态进入待对账

#### Scenario: Recovery returns real result

- **WHEN** 对已成功且结果可读的动作触发恢复
- **THEN** 恢复返回真实结果引用,不是占位文本

### Requirement: Synthetic corpus with version, ACL, and citation positions

场景包 MUST 内嵌少量合成文档,每份文档 MUST 携带版本、权限(ACL)标注和标准引用位置;语料清单 MUST 记录文档与属性。未授权文档 MUST 不可经检索或工具读取;引用 MUST 能定位到声明的引用位置。

#### Scenario: Citation resolves to declared position

- **WHEN** 场景断言引用来源
- **THEN** 引用可定位到语料清单声明的标准位置(如段落/表格单元格)

#### Scenario: Unauthorized document is not retrievable

- **WHEN** 以无权限主体检索受 ACL 保护的文档
- **THEN** 检索结果不包含该文档

### Requirement: Golden samples assert structure, not text

场景包 MUST 保留旧路径的响应协议与事件样本(golden 文件),以 stub 模型录制;断言 MUST 针对协议结构(字段、事件类型序列、状态语义),MUST NOT 对模型文本做逐字相等比较。golden 文件 MUST 标注录制时的代码路径与用途,作为后续入口迁移(如 step5)的比较基线。

#### Scenario: Protocol change breaks golden test

- **WHEN** 响应协议结构字段变化
- **THEN** 对应 golden 断言失败,提示协议漂移

#### Scenario: Model text change does not break golden test

- **WHEN** stub 模型的回答文本变化但协议结构不变
- **THEN** golden 断言仍通过
