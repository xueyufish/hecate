# 修复设计

- 宿主查询、取消按服务端 principal 与原 Run 数据域授权，证据查询按 principal 限定。
- 接受执行前持久写入证据；串行准入在 submit 内获取锁。取消仅在工具边界协作生效，关停清理在途任务且不得伪报成功。
- endpoint 配置必须实际调用配置端点；工具参数在网络分发前验证。未实现的 backend、工具映射和版本在启动期拒绝。
- 平台入口关联查询真实 AgentPrincipal，失败使用 savepoint 隔离并返回安全原因。评估传递 workspace、版本与共享存储，不跨 workspace 取 workflow。
- 对比演进方案与实现，保留未完成事项并记录证据，不将部分入口迁移声明为 Step5 全部完成。
