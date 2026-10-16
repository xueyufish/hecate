# Proposal

## Why

step5e 的技术切片(双路径停止语义、重放、结构化输出、子任务契约)已交付,但真实模型轨在归档复核中记录为 `skipped`(当时缺 `OPENAI_API_KEY`);当前凭据已具备。方案近期路径要求在 step5d 入口契约收口后完成 step5e 真实自主执行验收——关键未答问题:真实模型能否依据工具反馈完成"读取材料→按反馈选择后续工具→生成结构化结果",以及质量、延迟、成本是否达到预注册阈值。机制矩阵与 ADR-036 目前只有确定性证据骨架,等待实测数据定稿。

## What Changes

- **真实模型基线记录**:复用 `tests/scenarios/baseline/run_autonomous_baseline.py`,同一材料/工具/提示词/模型在 direct 与 Pregel 两条实际执行路径上运行全部案例,产出 `recorded` 报告(替代 `skipped`);工具选择、反馈参数(逐字 ticket_body)、结果正确性、schema 合规、零重复副作用逐项评分。
- **预注册验收标准**:沿用 rubric 阈值(延迟 60s、token 20k、成本 $1)并在运行前固化;成本不可核实保持显式未知(usage 记录 reported,价格未核实),不以估算冒充账单。
- **补齐两个真模型场景**(方案 step5e 勾选项中尚无证据的部分):审批等待→批准→继续推理至结构化产物;执行中途真实 TCP 断连→同会话恢复,依据持久事实续推且工具零重复(复用 G3 harness 模式 + 真实 provider)。
- **停止分支真模型验收**:预算耗尽、取消、未知写结果停止(采集器既有案例)在真实模型轮次上验证;每轮调用、工具参数、回执、停止类别完整留痕;确定性安全回归与模型质量报告分别保存。
- **文档定稿**:机制矩阵与 ADR-036 以实测数据更新;暴露的缺陷做针对性修正(修正本身附回归)。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `autonomous-execution-parity`: 新增"真实模型基线按预注册阈值记录"要求(含审批续推与断连恢复两个真模型场景、成本未知显式、记录不等于认证)。

## Impact

- 代码:`tests/scenarios/baseline/run_autonomous_baseline.py` 扩展(审批续推案例、报告字段)、新增真模型断连恢复场景模块(复用 `test_g3_real_tcp_disconnect` 的 harness 模式);若实测暴露产品缺陷,修正集中在执行链已交付文件。
- 运行:真模型运行经 `OPENAI_API_KEY`/`BASELINE_MODEL` 环境变量手动触发,不进 CI 断言(CI 保持确定性);报告写入 `tests/scenarios/baselines/`。
- 不变更:G3 默认开关维持关闭;独立 Runner 自主选工具、持久审批续推(宿主侧)、独立 workflow 部署/Run 身份另立 change。
- 完成标准:验证记录含**实际模型运行记录**(recorded 状态与逐轮数据);凭据缺失时只能显式 skipped 且不得宣告验收完成。
