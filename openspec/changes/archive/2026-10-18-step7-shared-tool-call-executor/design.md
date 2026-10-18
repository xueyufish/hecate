# Design

## Context

单次工具调用原由 ToolWorker._execute_single_tool 承载，其辅助方法只服务于这一调用链。批次 execute 负责提取消息、并发执行、排空异常、排序、citation 和 WorkerResult。后续 Controller 需要直接复用完整治理链，不能复制较弱实现。

## Goals / Non-Goals

- 提取具体类 ToolCallExecutor；ToolWorker 为实际消费者，不新增 ABC/Protocol。
- 保留既有身份、审批绑定、middleware 顺序、sandbox、恢复表、事件格式、账本与证据语义。
- 单次调用不加载 Pregel、compiler、channel、Worker/workers 等图执行机制。
- 不实现决策动作派发、Controller、多轮调度、持久等待、入口切流或新账本。

## Decisions

### D1: 单次调用与批次编排分离

ToolCallExecutor 构造参数与 ToolWorker 同名同序，直接持有 event_store 和治理依赖。execute_call 是原单次调用本体。ToolWorker 构造 executor，_execute_single_tool 为薄委托；execute、_extract_tool_calls、_apply_citation_provenance 留在 Worker。

迁入的辅助方法：
- _recovery_outcome、_log_recorded_content、_hook_resolution、_hook_claim、_mirror_outcome、_checked_recovery_result、_append_tool_call；
- _check_access、_withheld_result、_capture_evidence、_emit_channel_write_rejected；
- 模块函数 _is_indeterminate_error、get_tool_receipt、_merge_resolutions 及其判定常量。

tool_worker 保留既有模块恢复符号的兼容 re-export。治理字段仅存于 executor，不在 Worker 中维护第二副本。既有装配/替身测试跟随归属调整为 worker._executor，保留原拒绝、属性身份与归属断言，避免对失效字段的写入使测试空过。

### D2: sandbox router 是共享辅助模块

原 workers.sandbox_router 导入会执行 workers/__init__，间接加载 Coordinator/Pregel；仅将导入延迟到构造函数无法解耦。原具体实现移至 hecate_runtime.sandbox_router，旧路径仅转发同一模块/类。executor 从共享位置顶层导入，旧平台 shim 和装配路径兼容。该迁移属于共享组件依赖闭环，不改变 sandbox 行为。

### D3: provenance 与兼容缺省

稳定恢复需要 session_id 和非空 tool-call ID；legacy 调用缺失任一标识时保留原随机 ID 回退，不承诺稳定恢复。独立调用方仍须提供可信权限、主体、数据域等既有治理上下文，组件不授予授权。

superstep/trace_id 仅为事件元数据，缺省为 0/None；所有单次事件写入统一读取该缺省，既有图路径的值原样保留。node_id 是证据调用方标签；事件回执保持原 node_id=None，以 execution_id/tool_call_id 关联，不新增或改写事件格式。agent/workspace/environment/evidence_tracker 使用原 dict 键约定。citation 标记继续由 Worker 批次写回负责。

### D4: 双入口与真实恢复证据

相同用例经 execute_call 或真实 ToolWorker.execute 运行，后者不得只调用其私有薄委托。覆盖参数错误、访问拒绝、审批拒绝/通过/无 callback、middleware 与 legacy hook、sandbox、成功/失败/未知、身份和证据、账本 claim/outcome 与真实结果回填。

SQL 集成测试关闭并重建 store/hook/executor，不保留事件日志或通道历史，验证真实结果回填及回执丢失后停止待对账。它证明账本消费与对象重建恢复，不冒充跨进程认证。受控并发测试强制两个预检同时 miss 后竞争 claim；批次测试挂起已启动业务调用，确认异常发生后 Worker 仍等待排空，并验证逆序完成时保持输入顺序。citation 经既有套件回归。

### D5: 冷进程解耦探针

全新 Python 子进程在首次 runtime 导入前安装阻断器，禁止 Pregel、compiler、channel、Worker/workers、scheduler、checkpoint；随后导入并构造 executor，注入真实 InMemoryEventStore 和 StubRuntimePort，在无 superstep 的上下文完成只读调用。父进程和测试收集期间的 sys.modules 缓存不参与证明。

## Risks / Trade-offs

- 大段移动容易遗漏 helper 或装配字段：以 AST 比较单次方法及保留批次方法；正常图上下文路径保留原行为，superstep 缺省适配有专门测试。
- 失败不视为已通过：跳过项、环境前提和实测结果分别记录，不将隔离复跑泛称为全套通过。
- Runtime 不引入 durable、Runner 或平台业务依赖；SQL 消费测试留在平台测试侧。

## Migration Plan

代码与测试一起落地，无数据或配置迁移。应用回退可 revert，本 change 不修改持久事实、稳定身份或路由配置。
