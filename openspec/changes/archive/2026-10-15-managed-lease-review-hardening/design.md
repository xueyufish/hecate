# Design

## Context

见 proposal.md。#251 是此前复核修正，#252 为无代码归档；本轮行为修正集中 #253。现有 CLI 将 ManagedLeaseProfile 装在所有运行上，审批桥测试只调用私有租约门。平台事件主键为 UUID，而迁移测试用自增整数；位置竞争测试没有制造真实首次分配冲突。

## Goals / Non-Goals

Goals：真实派发保持两种授权源组合、scope 与安全底线；平台与宿主窗口分别执法；迁移在两种方言上成立，事件异体重放不能覆盖投影。

Non-Goals：不启用受保护写入，不替代 step16 认证；缺省版本配置兼容，不引入强制 Agent 身份或新依赖。

## Decisions

- PolicyContext 增加由执行器赋值的审批引用与受管来源。受管 profile 用既有 ApprovalGate 和权威 ledger 重验完整绑定，不能只信 boolean。引擎只保留该动作的消费关联；独立请求选择 inner profile。审批并不覆盖 inner DENY/撤销，已验证租约 scope 仍限制审批补位。重启后的空租约门不放宽作用域。
- LeaseGate 对所有期限判断先验可信时间，配置窗口要求有效 iat，参数必须有限且为正。平台签发把 exp 收紧至 min(TTL, enrollment窗口)，不增签名字段。
- 指纹从本地 profile 文档按字段去掉已解析 secret 值，包含授权/身份声明与配置及执行定义；运行期授权文件变化能被下一次上报捕获。上传成功不清除漂移；通道认证明确观察到信任撤销后合并到治理撤销视图，重新验证成功才清除。
- 原迁移使用类型化 SQLAlchemy 表达式实现 null-safe 比较与 UUID 保留顺序（created_at、id），不把随机 UUID 次序当插入次序。补充新迁移/模型部分唯一索引，覆盖 workspace=NULL 位置；升级仅操作明确同位重复并留日志。
- 竞争 event_id 去重必须比较除到达序/接收时间外的事实；异体或跨 workspace 同 ID 抛显式冲突。投影器不能把竞争输家输入折叠成已存事实。

## Risks / Trade-offs

- [审批重验成本] → 复用现有 ledger 与 CAS，不增加远端依赖；消费关联属于逻辑 action。
- [指纹算法变化] → 操作员需按新算法重新登记 expectation；漂移期间证据上传可继续。
- [已部署迁移] → 原迁移修复仅影响尚未执行的重复修复分支；新索引单独追加迁移，不回写 alembic 版本。
- [多连接并发认证] → 增加确定性碰撞测试，实际 PostgreSQL 能运行时补验，否则记录未认证。

## Migration Plan

保持既有有 workspace 唯一约束，新增无 workspace 部分索引。执行 expand 升级后部署代码；回退新索引不影响原约束。既有重复修复按真实时间戳确定保留行并报告删除记录。
