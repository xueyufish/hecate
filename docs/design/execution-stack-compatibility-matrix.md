# Execution Stack Compatibility Matrix

执行栈组合的契约/依赖矩阵（step5d；step6 增补持久核心包）。发行单元：平台主包
`hecate`、内核包 `hecate-runtime`（含 `contracts/execution` 契约与 schema，随内核发行）、
持久核心包 `hecate-durable`（durable 契约集群 + SQL 参考存储，step6 起
`hecate.contracts.execution` 的 durable 集群经 shim 指向此包）、宿主包
`hecate-runner`（durable profile 依赖 `hecate-durable`）。矩阵列出受支持组合、
兼容边界与退出条件；矩阵外组合不视为受支持。

## 当前受支持组合

| 组合 | 平台 `hecate` | 内核 `hecate-runtime` | 持久核心 `hecate-durable` | 宿主 `hecate-runner` | 验证方式 | 状态 |
|---|---|---|---|---|---|---|
| workspace 联动开发 | workspace 源（uv source） | workspace 源 | workspace 源 | workspace 源 | 全量测试套件 + 包测试 | supported |
| 三 wheel 干净安装 | — | `0.1.0`（wheel） | `0.1.0`（wheel） | `0.1.0`（wheel） | CI `runtime-wheel-clean-install`（干净 venv + 冒烟 + CLI + durable 独立导入检查） | supported |
| 平台消费内核 wheel | workspace 源 | `0.1.0`（wheel） | `0.1.0`（wheel） | — | CI wheel 构建 + 无 hecate 泄漏断言 | supported |

## 边界与退出条件

- **版本窗口**：`0.1.0` 为技术预览线，无 semver 兼容承诺；wheel 只按已测试
  版本组合发布（同一提交构建），不允许跨提交混装未验证组合。
- **升级**：内核与宿主可单独升级，但升级后的组合必须重新通过上表验证方式
  并更新本矩阵；平台旧版本消费新内核 wheel 需要契约 schema 版本兼容
  （`contracts_version` 0.1 内只允许增量字段）。
- **回退**：发行包退回旧版本前必须核对制品/状态格式兼容（事件 envelope、
  run 记录）；禁止静默回退到省略身份或审计的裸执行入口。
- **契约拆包**：`hecate-contracts` 不单独发行，随 `hecate-runtime` 交付；
  durable 契约集群自 step6 起随 `hecate-durable` 交付（原路径纯转发）。
  待 step8 出现第二个消费者时再评估进一步拆包。
- **退出**：`0.1.0` 组合在发布线建立（step8/step19 依赖路线图）后由新矩阵行
  取代，旧行标记 `retired` 并保留可追溯。

## 管理方式

本矩阵由 `scripts/check_execution_stack_matrix.py` 在 CI 中校验：矩阵声明的
当前版本必须与 workspace `pyproject.toml` 的实际版本一致；修改任一包版本时
必须同步更新本表。
