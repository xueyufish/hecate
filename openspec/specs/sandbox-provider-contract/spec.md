# sandbox-provider-contract Specification

## Purpose

定义进程外 Sandbox 提供方的最小契约:环境与命令的幂等标识、生命周期状态映射、命令结果未知的显式表达与按能力协商的可选状态,使云端/自托管环境服务以独立于执行契约的方式接入与版本化。

## Requirements

### Requirement: 环境与命令的幂等操作集

`SandboxProvider` 最小契约 MUST 覆盖:能力发现、创建/查询环境、提交/查询命令、文件上传/下载、终止与续租。环境创建与命令提交 MUST 各自接受幂等 ID:以相同 ID 重复创建请求 MUST 返回对同一环境的引用而非第二个实例;以相同命令 ID 重复提交 MUST NOT 产生第二次副作用。文件传输 MUST 以引用(而非宿主路径或容器对象)表达。

#### Scenario: 重复创建请求幂等

- **WHEN** 创建响应丢失后以相同幂等 ID 重发创建请求
- **THEN** 返回原环境的引用与状态,不创建第二个实例

#### Scenario: 重复命令提交无第二次副作用

- **WHEN** 命令提交响应丢失后以相同命令 ID 重发
- **THEN** 返回原命令的查询引用,命令不在环境中第二次执行

### Requirement: 状态映射与命令结果未知

环境状态 MUST 映射为:创建中、就绪、终止中、已终止、失败、状态未知。命令执行超时或连接中断 MUST 表达为结果 unknown/待对账,MUST NOT 自动判定成功或失败,恢复后 MUST 以命令 ID 对账而非盲目重试。请求终止 MUST NOT 被表述为实例已销毁;失联 MUST NOT 被表述为资源已释放。

#### Scenario: 命令超时进入待对账

- **WHEN** 命令执行超过期限无结果返回
- **THEN** 状态为 unknown/待对账,对账路径以命令 ID 查询真实结果,不重发命令

#### Scenario: 终止请求与实际销毁分离

- **WHEN** 查询一个已请求终止的环境
- **THEN** 状态反映终止中或已终止的实际阶段,不把"已请求"表述为"已销毁"

### Requirement: 可选能力按能力协商

暂停、恢复、快照、fork、浏览器/桌面、GPU 等能力 MUST 分别声明,未支持的能力 MUST 显式返回 unsupported,MUST NOT 因提供方类型(SaaS、microVM、容器)推断为已支持。契约 MUST 独立于执行契约携带自己的版本号;执行契约的破坏性变更 MUST NOT 强制 Sandbox 契约同步升版。

#### Scenario: 不支持快照的提供方显式拒绝

- **WHEN** 向未声明快照能力的提供方请求快照
- **THEN** 返回结构化 unsupported 错误,不返回部分快照或伪成功

#### Scenario: 版本独立演进

- **WHEN** 执行契约升版而 Sandbox 契约未变
- **THEN** Sandbox 提供方无需变更即继续满足其契约版本
