# 复核记录 — step2-agent-native-baseline-alignment

## 结论与范围

已对照 `origin/main` 的 `1017abdf`、演进方案、当前执行服务/宿主边界和本 change 的 proposal/design/delta/tasks 完成复核。下列问题已在同一 worktree 修正，当前文档/规范增量/规划数据范围内通过本地复核。

本次不改变运行时代码、入口路由、默认开关、数据库或主 specs；没有提交、推送、归档，也没有新增运行能力或授予生产认证。

## 发现并修正的问题

1. **严格增量准入失败。** 原实施只运行普通 `check`，以 `origin/main` 为基准运行增量检查会拒绝已修改的 `2.10c/2.10d`：缺 `maturity/provider_or_adapter/superseded_by`。现补齐 `unverified/pending/n/a`，外部 Runtime 专属条目的当前 M-A 归属撤销，旧证据和条件明确作为历史记录保留，未提升成熟度。
2. **新版路线图门槛未完整同步。** I-Ca/I-Cb/M-S 漏了 Controller、共享可靠操作、最小交付/版本化验收、预算累计和文件 profile 的条件性恢复；I-F/M-C 与 core-06 仍要求跨 Runtime 协作，catalog 的非受管描述仍含 step8 认证承诺。现从规划数据源修正并再生成，同时明确 M-A/I-Cb 优先、I-Ca 独立验收而非整体前置，以及 I-D/M-Aa 的历史地位。13.15 保留 ID，当前标题和验收聚焦内置执行团队。
3. **新 Feature 的映射与责任不一致。** `1.3.11l` 原先挂在 provider-guarantee/builtin-reference 的 review-01，与其 platform-guarantee/core-contract/builtin 字段不符。现归入 core-04a，并区分已交付 1.3.11h 的兼容语义证据与尚待交付 Controller 的新验收；旧条目不追授新支持。
4. **ADR 的依据与所有权表述不准确。** 原 ADR-037 将架构选择写成已有实测结论，并沿用“序列化计划可审计、代码不可审计”的排除推论。现明确新 Controller 尚无比较实测，程序执行依赖授权、隔离及证据认证；决策/计划语义由 Controller/Workflow 拥有，共享机制承担持久提交与恢复，不形成第二个业务状态真源。旧 ADR 正文保留，ADR-034 的外部宿主语义加历史范围说明。
5. **Delta 容易追授未实现能力，Purpose 同步缺少明确输入。** 可靠计划修订要求现限定到后续声明支持且通过验收的 profile，并增加既有 DYNAMIC 不自动取得新保证的场景。两个 delta 均提供目标 Purpose，供归档同步时显式更新；本次未提前修改主 specs。
6. **验证记录不足以证明元数据保留。** 普通 catalog/YAML 一致性检查不能证明历史手填字段无丢失。现额外程序化比较基线数据、历史 ADR、Runtime 自足清单、旧路由场景和生成文件散列，并记录下列实际结果。

## 实际验证

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe scripts/dev.py check all`（等价于共享 shell 入口） | ruff check 通过；format 检查 1578 文件；mypy 933 源文件无错误 |
| `.venv/Scripts/python.exe scripts/dev.py test tests/test_scripts/test_feature_inventory.py -q` | **62 passed** |
| `python scripts/feature_inventory.py check --strict --base-ref origin/main` | **413 entries, 0 errors, 189 warnings，退出码 0**；仅历史未修改债务留存 |
| `python scripts/feature_inventory.py sync --check` | 无受管区域漂移 |
| 二次实际运行 `feature_inventory.py sync` 并比较 SHA-256 | catalog、roadmap、inventory 内容散列不变 |
| `openspec validate step2-agent-native-baseline-alignment --strict` | valid |
| `git diff --check` | 通过 |
| 原始 `origin/main` inventory/catalog 的普通检查 | 412 entries, 0 errors, 191 warnings；本次补齐两个既有条目的必需字段后减少为 189，并非忽略新增失败 |
| 逐字段比较 inventory | 原有 412 个 ID、status、dependencies、非空 maturity 和 profile_support 保留；旧非空 evidence 内容仍完整包含在记录中；只新增 planned/unverified 的 1.3.11l |
| ADR/边界/场景核对 | ADR-001/007/032 的 Context 起全部正文保留；Runtime 自足约束及 sanctioned imports 清单不变；聊天 delta 的原有 7 个路由场景逐字保留 |
| 新增本地 Markdown 链接与修改范围 | 24 个新增链接目标存在；无 src/ 或主 specs 改动 |

## 保留限制与生命周期

- 主 specs 仍按当前实现保留。归档同步时必须显式采用两个 delta 的 `## Purpose` 内容并复核 Requirements，不能仅添加要求后留下“统一 Pregel”总目标；当前没有执行同步或归档。
- Controller、可靠决策/计划恢复及相应生产退出门槛仍待后续 step5e/6/7 交付，历史技术切片与真实模型观察不自动覆盖这些新要求。
- 历史未修改 inventory 债务保留；本次没有全量回填或改写既有支持矩阵。
- 本地验证不代替尚未执行的 CI、PR 审阅或部署认证。
