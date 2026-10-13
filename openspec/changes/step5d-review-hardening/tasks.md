# Tasks

## 1. 入口契约修复

- [x] 1.1 将最终结果处理纳入成功投影前的边界，保留停止/结构化流式关联，以真实 HTTP 输出校验拒绝与修复回归验证。
- [x] 1.2 入口服务统一落流式信封并保存有界摘要，缺关联也剥离内部字段，以跨 mapper 分页和缺关联回归验证。

## 2. 定时 Workflow 兼容与隔离

- [x] 2.1 保留 workflow input_data 的 channel 值，拒绝内部/sys 输入覆盖，以真实执行服务业务 channel 回归验证。
- [x] 2.2 按调度 workspace 拒绝外部目标、使用共享工具 registry，并明确 Task-only 关联，以定时入口与跨 workspace 负例验证。

## 3. 文档与验收

- [x] 3.1 修正文档完成声明、入口矩阵状态和证据路径，交付包含发现/修正/保留缺口的 review.md，逐项核对源文件及测试。
- [x] 3.2 执行 ruff check、ruff format --check、mypy src、受影响入口/执行/workflow/分层回归和 OpenSpec strict validate，记录实际结果。
