# Tasks

## 1. 模型与迁移

- [x] 1.1 `src/hecate/models/evaluation.py`：`EvaluationRunModel` 新增 `agent_id`、`agent_version`、`agent_config_hash` 和 `agent_snapshot`（nullable、索引；docstring 说明绑定语义与 NULL=未绑定），读取 schema 暴露 Agent 身份、版本和配置哈希；不扩展 POST /runs。验证：mypy 通过；schema 往返含新字段。
- [x] 1.2 新增 expand-only alembic 迁移（加列，无数据回填，有绑定证据时拒绝删列降级，无绑定证据时可降级）。验证：`alembic upgrade head` 在本地 PG 通过；`alembic downgrade -1` 后再 upgrade 复原；`alembic heads` 单一。

## 2. 评测执行绑定

- [x] 2.1 `AgentVersionService` 新增从 `ResolvedAgentConfig` 构建 agent definition 的纯函数（含 pin 的 `workflow_version`），快照结构知识留在 versioning 侧。验证：单测覆盖 live 与 version 两种来源的 definition 形状。
- [x] 2.2 `EvaluationEngine.run` 新增 `agent_version: int | None = None`：开始阶段经 versioning 服务解析（版本不存在/跨 workspace → Run 失败终态 + 原因，不回退 live），落列 `agent_version` 并以解析结果构建 `agent_definition` 走既有注入路径；未指定时行为与现状逐字节一致。验证：单测覆盖绑定执行、失败开始、live 默认不变三态。
- [x] 2.3 任务 runner：`config.get("agent_version")` 透传进 `run_kwargs`（镜像 `workflow_version` 分支写法）。验证：任务触发路径单测覆盖声明/未声明两态。
- [x] 2.4 任务 schema/config：`EvaluationTaskCreate/UpdateSchema` 增加可选 `agent_version`（ge=1），service 校验 `agent_version` 仅在 `answer_source=agent` 时声明并写入 config。验证：API 测试覆盖声明/缺省/非法组合（非 agent 来源声明即 400）。

## 3. 发布门禁版本匹配

- [x] 3.1 `AgentVersionService._latest_completed_run` 增加 `version` 过滤（按 Run 自有身份、哈希和 workspace 过滤）；`_publish` 传待发布版本；`_evaluate_gate` baseline 改为 `published_version` 的匹配 run；`require` 模式候选缺失时拒绝原因标明"缺少匹配版本的评测"。验证：单测覆盖匹配/缺失/跨 workspace 三态。
- [x] 3.2 `publish_gate.py` 零修改核对：调用方补足版本、基线及必要快照前置条件。验证：`git diff` 不含 `publish_gate.py`；既有 publish_gate 测试零放宽全绿。

## 4. 回归与验证

- [x] 4.1 门禁行为回归（主断言）：v1 全通过 + require → 发布 v2 拒绝（原因=缺匹配评测）；v2 绑定运行信号失败 → 拒绝且报告含该运行；信号通过 → 放行；force → 放行 + `_record_gate_bypass` 审计 + 报告标 bypass 非通过；warn → 报告不阻断。验证：`tests/test_api/test_agent_versions*` 或版本化服务测试目录内新增用例全绿。
- [x] 4.2 执行绑定回归：绑定 v2 后草稿改并提交 v3 → 原 run 的 `agent_version` 与证据不变；历史 NULL 行不参与任何版本匹配；pinned workflow 版本被执行（复用 `test_workflow_version_resolution` 的解析断言模式）；声明不存在版本 → 运行失败开始；跨 workspace → 拒绝。验证：`tests/test_execution/` 或评测相关目录新增用例全绿。
- [x] 4.3 既有回归显式收集（根 `testpaths` 只含 `tests/`，注意包内测试）：`tests/test_api/` 评测与版本化相关文件、`tests/test_execution/` 评测相关、`src/hecate/ops/evaluation` 触及的单测目录；报告实际收集数。验证：全部通过，既有断言零放宽。
- [x] 4.4 共享检查 `bash scripts/dev.sh check all`。验证：0 错误。
- [x] 4.5 汇总实际结果到 `verification.md`（各命令输出要点、收集数、flake 判定如涉及、未完成项与保留限制）。验证：实际结果已记录；提交、推送与 PR/CI 留给后续用户操作。

## 5. 复核修正

- [x] 5.1 绑定的 Agent 身份、配置摘要及快照落到 Run 自身；候选不再依赖任务当前 config；修复跨 workspace 校验及任务更新的版本参数保留。
- [x] 5.2 绑定运行强制实际生成答案，保留空配置、快照工具及 Skill pin；非 chat 模式复用已存在的版本化执行入口。
- [x] 5.3 版本匹配成为独立必需条件；warn/force 返回真实报告，force 的审计失败不放行；迁移保留已写入绑定证据。
- [x] 5.4 添加漏洞复现回归并完成共享检查、适用测试、迁移及 OpenSpec 验证，记录实际支持范围。
