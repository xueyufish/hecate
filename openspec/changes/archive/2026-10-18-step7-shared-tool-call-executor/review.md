# 复核记录 — step7-shared-tool-call-executor

## 结论

已在原 feature worktree 修正发现的问题。交付仍限定为共享单次工具调用组件的提取和 Worker 委托；不包含已接受动作派发、Controller 或入口切流。实际命令和结果见 verification.md。

## 已修正问题

### 独立构造仍隐式加载 Pregel

executor 构造时延迟导入 workers.sandbox_router，仍会先执行 workers/__init__，再由 Coordinator 导入 Pregel。用全新进程阻断 hecate_runtime.pregel，原代码在构造阶段立即 ImportError。原测试先收集 ToolWorker，仅从 sys.modules 移除少数模块，已缓存的 workers 包掩盖了传递依赖。

将原 sandbox router 实现移至 runtime 根，旧路径转发同一模块/类；executor 顶层导入共享实现。冷进程探针在首次 runtime 导入之前阻断整个图/Worker 相关模块，注入真实 EventStore 完成一次调用。

### 独立 provenance 契约与事件实现不一致

规范称 superstep 可省略，三个事件写入点却使用下标读取。仅提供 session_id 时，未知/写工具被误判存储故障而拒绝；只读工具则可先执行业务，再在写回执时触发 KeyError。

各写入点统一使用 superstep 缺省 0，显式值和 trace_id 原样保留。新增两入口的无图元数据调用测试，核对真实 TOOL_CALL/TOOL_RESULT、稳定 ID、证据标签和显式图元数据。事件 node_id 原本为 None，继续保持；修正了 delta 中“回执携带节点标签”的错误声明，节点标签属于 EvidenceTracker。

### 现有装配与替身注入测试未随归属迁移

聊天装配测试读取已移除的 Worker 治理字段而失败；tenant attribution 测试将策略替身写入失效字段，真实 executor 仍使用原策略。monotonic denial 测试改写失效规则字段也可能空过。

将这些测试的观察/注入位置跟随归属调整至 worker._executor，保留原有拒绝和归属断言，并同时验证 executor 和 Worker 使用同一 EventStore。

### 测试覆盖与验证记录不符

原新增套件没有声称覆盖的 middleware、未知结果、ActionLedgerHook 故障用例；Worker 参数实际直呼私有委托，未经过公开批次入口。批次顺序测试未强制逆序完成，也未触发异常排空。

改为直接 executor / 真实 ToolWorker.execute 双入口，补齐承诺场景；以事件屏障制造领取竞争和挂起的业务调用；在平台侧增加真实 SQL store/hook/executor 重建及回执丢失集成测试。重写 verification，不沿用无对应证据的计数或认证声明。

## 语义对照

- 移动的单次方法逐个 AST 对照 HEAD，除 docstring 和声明的 superstep 缺省适配外一致。
- execute、_extract_tool_calls、_apply_citation_provenance 的 AST 与 HEAD 完全一致。
- 共享 sandbox router 原实现的 AST 与 HEAD 完全一致。
- 新增共享模块已加入运行时业务自足子进程探针，未新增 ABC/Protocol 或 runtime 对 durable/Runner/平台的反向依赖。

## 环境核实

扩展回归中 shell hook 的 JSON stdin 测试调用 cat，PowerShell 启动的子 shell 缺少该命令。已直接加载 HEAD 原实现复现相同 command-not-found；仅对该测试进程临时补入已安装的 Git usr/bin 后，原测试通过。未修改 shell hook、系统 PATH 或该测试断言。

## 保留边界

SQL 恢复测试证明对象/连接重建，不冒充新进程认证。冷进程探针证明导入和单次执行解耦，不宣称完整 Controller 已交付。跳过的可选 PostgreSQL 等测试不计为通过。未提交、推送、归档或创建 PR，CI 与 PR review 仍由后续流程完成。
