# Tasks

## 1. 审查基线

- [x] 1.1 核实 main 与历史审查合入，阅读 Step1～Step6 和新 change 的设计/规范，记录基线。
- [x] 1.2 补复现测试，证明等待前写入、内部 grant、命令异体重放、checkpoint 跨会话和恢复门禁缺陷。

## 2. 修复实现

- [x] 2.1 修复 Runner 唤醒 CAS、原请求绑定、动作来源保留、输入合并与 attempt checkpoint 隔离；运行等待/恢复测试。
- [x] 2.2 修复授权拒绝、未知动作和结果持久化失败的后续停止；运行实际副作用计数负例。
- [x] 2.3 修复平台命令/回调授权与可信字段，平台普通历史恢复收紧；运行任务控制/回调/worker 回归。
- [x] 2.4 修复 checkpoint 跨会话读取与受管混合来源/历史 Run 上传；运行存储和受管通道回归。
- [x] 2.5 修复执行时 Principal/部署/版本重验、Runner 定义指纹与 Lease issuer/subject/tenant 绑定，验证旧任务保守迁移与任务读取隔离。

## 3. 验证与文档

- [x] 3.1 回归契约、分层、Runner/runtime/durable 与 Step1～Step6 相关测试，执行 ruff/format/mypy，记录环境与跳过项。
- [x] 3.2 执行安装制品进程场景和隔离 PostgreSQL 验证；失败或不可运行项明确登记，不以组件测试替代。
- [x] 3.3 编写审查报告、更新演进方案与能力边界，OpenSpec strict validate 通过。
