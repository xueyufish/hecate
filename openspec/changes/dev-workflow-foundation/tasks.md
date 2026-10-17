# Tasks

## 1. 统一开发入口

- [x] 1.1 实现 sync、doctor、固定检查和显式 pytest 入口；通过隔离测试验证锁定安装、本地解释器选择及退出码传播。
- [x] 1.2 worktree、direnv 和 CI 复用统一环境及检查命令；通过配置回归测试和真实锁定同步验证一致性。

## 2. Git hooks 与流程

- [x] 2.1 实现可恢复的转发 hooks 安装及当前工作树路由，统一 hook 检查命令；通过临时 Git clone/worktree 测试验证保护与路由。
- [x] 2.2 对齐 AGENTS.md、README、开发流程与 PR 模板；通过文档一致性测试和人工审阅确认生命周期与验证规则无冲突。

## 3. 综合验证

- [x] 3.1 运行统一 lint、format、types、受影响脚本测试、shell 语法和 OpenSpec strict 校验；记录实际结果并检查 main 保持不变。
