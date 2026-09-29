# Tasks

## 1. 修复及反例

- [x] 1.1 补 MCP viewer 写文件反例并修复角色门槛；验证文件未创建。
- [x] 1.2 补事务锁正常、异常退出及超时测试并修复 Postgres 锁与版本分配 SQL；添加 opt-in 真实 PG 池复用回归。
- [x] 1.3 补工具身份变化、非法参数、遗留摘要、未知状态与未决幂等写反例；修复恢复查询及重试领取并验证定向测试。

## 2. 集成验证与证据

- [x] 2.1 更新基线和 Step1 关闭证据，明确真实数据库验证限制；检查文档 diff。
- [x] 2.2 执行相关 runtime、MCP、REST、scenario 和 event-state 测试，以及 ruff、format、mypy 和 OpenSpec strict 校验；记录结果。

## 验证记录

- 修复前：新增 ToolWorker 反例出现 12 个失败；锁生命周期 SQL 单测出现 2 个失败。
- 真实 PostgreSQL 首轮：锁释放/超时 3 项通过，持久化领取与并发 append 2 项失败，错误为 aggregate FOR UPDATE 不被支持；修复后通过。
- 综合定向回归：171 passed、1 skipped、2 deselected，覆盖恢复/receipt、双实现契约、event-state、MCP、REST G1 与全部场景包。
- 批量原子性与混合会话补充后：Postgres 单测和真实集成回归 20 passed。
- 唯一 skip 是 Windows 无符号链接创建权限的跨工作区 symlink 负例；非生产后端能力通过的证据。
- ruff check、ruff format --check、mypy src/ 和 OpenSpec strict 校验通过；git diff --check 无空白错误。
- 依赖运行时复用主 checkout 的已安装环境，PYTHONPATH 显式指向本 worktree 的 src 与 workspace packages；真实 PG 使用独立临时 postgres:16 容器，已停止。未使用业务数据库、未 push、未归档。
- 最小安全保护已收紧；生产高并发池容量、统一 Action 持久化及实际供应商托管执行认证仍未完成，不构成 Step1 的生产就绪声明。
