# Verification review

## Scope

本 change 仅更新演进计划及实践对照，固定已确认的目标架构与实施/验收依赖。完整 spec-driven 保存 proposal/design/tasks；因本次不改变产品运行行为，skip_specs 明确生效。主 specs、既有 ADR、运行时代码、数据库、Feature ID、默认开关与历史证据不变。

## Observed results

| 命令或检查 | 实际结果 |
|---|---|
| `openspec validate agent-native-evolution-plan --strict` | 通过；CLI 明确接受 skip_specs 与零 delta |
| `python scripts/dev.py sync --profile lint` | 锁定环境安装成功；安装 PyYAML 与 Ruff，硬链接不可用时正常回退复制 |
| `python scripts/dev.py check lint` | 通过，All checks passed |
| `python scripts/dev.py check format` | 通过，1576 files already formatted |
| `python scripts/feature_inventory.py check --base-ref HEAD` | 411 entries，0 errors，191 warnings；0 new/changed entries，工具明确报告未变更历史债务 |
| `git diff --check` | 通过 |
| 下方文档审计 | 两份文档的本地链接目标、表格列数、围栏、原步骤标题与已完成勾选保留；step6a—6f 原交付行完全保留；新旧架构描述检查通过 |
| 差异人工复核 | 目标、工作包、依赖、迭代、验收矩阵和实践裁决一致；审批续跑链接改为实际 archive 路径 |

命令均在新 worktree 执行，检查使用其 `.venv/Scripts/python.exe`。首次环境同步由原 checkout 的 Python 启动 worktree 的标准库 CLI，未复制或共享虚拟环境。

## Reproducible document audit

在 worktree 根目录将下列 Python 块交给 `.venv/Scripts/python.exe -` 执行。HEAD 是本次 worktree 的未修改基线；只检查本地链接目标是否存在，不声称重验所有外部资料或 URL 可达性。

```python
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote

paths = [Path('docs/refactor/enterprise-agent-platform-evolution-plan.md'),
         Path('docs/refactor/agent-platform-practice-review.md')]
for path in paths:
    old = subprocess.check_output(['git', 'show', f'HEAD:{path.as_posix()}']).decode('utf-8')
    new = path.read_text(encoding='utf-8')
    completed = [line for line in old.splitlines() if line.startswith('- [x]')]
    normalized_completed = [line.replace('P/SC 场景映射', 'SC 场景映射') for line in completed]
    assert all(line in new.splitlines() for line in normalized_completed), path
    for pattern in [r'^### step\d+ .+$', r'^\| step6[a-f]：.+$']:
        assert re.findall(pattern, old, re.M) == re.findall(pattern, new, re.M), path
    local = external = 0
    for target in re.findall(r'\[[^\]]*\]\(([^)\n]+)\)', new):
        if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', target):
            external += 1
            continue
        target = unquote(target.split('#', 1)[0].strip('<>'))
        if target:
            assert (path.parent / target).exists(), (path, target)
            local += 1
    width = None
    for line in new.splitlines():
        if not line.startswith('|'):
            width = None
            continue
        current = len(re.split(r'(?<!\\)\|', line)) - 2
        width = current if width is None else width
        assert current == width, path
    assert new.count('```') % 2 == 0, path
    print(f'{path.name}: local={local}, external_unchecked={external}, preserved_completed={len(completed)}; structure valid')
plan = paths[0].read_text(encoding='utf-8')
for stale in ['内部是否共用 Pregel 由 step5e 的证据决定', '是否抽取独立 harness',
              '内部边界由 step5e 验证', '../../openspec/changes/step5e-durable-approval-continuation/review.md']:
    assert stale not in plan, stale
for expected in ['PlanProposal', 'AcceptedPlanRevision', 'Child Run', '不依赖 Pregel',
                 'M-A/I-Cb', 'step8（撤销）', '新增共同执行语义', '规范与 ADR 迁移清单',
                 '任务完成、证据与工作环境', 'Evidence Receipt', '不自动回滚文件系统',
                 '累计预算', '必需检查失联', '研究与文件交付', '受控业务操作',
                 '完整 Batch 不作为 M-A/I-Cb 的前置', '无进展保护与合理等待',
                 '企业级 Agent 原生、持久化、强治理智能执行平台',
                 '将执行宿主纳入 Hecate 控制面统一管理']:
    assert expected in plan, expected
assert not re.search(r'Problem_Lab|P0[1-8]|工作簿|P/SC|SC/P/S', plan)
print('Architecture decisions, profile boundaries, scope and stale decision checks passed')
```

## Limits and follow-up

- 文档修订未新增 runtime 行为，未运行产品 pytest、真实模型、数据库故障矩阵或 typecheck；仅建立 lint profile，未将这些检查计为通过。代码实施需按新切片运行相应完整检查。
- 现行 unified-chat-execution、dynamic-orchestration、durable-execution-contract 及图优先 ADR 的迁移已登记至 step2/3/5/6，本次不提前修改其历史含义。定位、功能清单、roadmap 和 Runtime 说明由所属 change 同步。
- 未推送、未创建 PR，CI 与外部 PR review 尚未运行。主 checkout 保持原状态。
- 仓库 Bash 启动脚本在当前 Windows 环境返回访问拒绝，使用 Codex create_worktree 创建并挂接隔离 checkout，再创建 docs 分支。中途一次写入因自动审批服务用量限制未执行；用户要求继续后重新通过审批，核对状态后完成。

## Follow-up: delivery, acceptance and working environment

本轮仅补充主方案及本 change 的 proposal/design/tasks/review，实践对照文件保持上一轮修订。继续沿用原分支和 worktree；目标架构、步骤编号与历史完成范围保留。

- 新增执行、交付、验收与业务效果的责任分工；最小 Artifact/Acceptance、稳定 Evidence Receipt 和受控续跑纳入首次自主闭环，不等待企业 Team。
- 文件工作区与平台 Workspace 分开；文件 profile 需验证产物提交、版本匹配及实际后端写入保护，数据库 ownership 不冒充远端命令已停止。纯 API profile 不强制安装 Sandbox。
- 同步 step2/3/5e/6/7/9/10/13/14/16、I-Ca/I-Cb、M-S/M-A、功能归属与验收矩阵。Batch 按需复用既有持久机制；新增研究交付与受控业务操作样本，不将样本通过等同生产认证。
- 追加 task-run、agent-environment、sandbox-environment-mount 和 sandbox-provider-contract 的规范迁移责任；复用现有 Task 字段，不新增 Goal/Receipt 权威存储。

本轮实际复跑 OpenSpec strict、lint、format、feature inventory 与 git diff --check，均通过；inventory 仍为 411 entries / 0 errors / 191 warnings，0 new/changed entries，CLI 明确列为未变更历史治理字段及证据债务。文档审计再次通过：主方案 44 个本地链接、15 条历史完成项，实践对照 7 个本地链接；原 step 标题和 step6a—6f 交付行原样保留。上方审计代码增加本轮契约覆盖检查，可重复执行。Markdown 采用 LF。

设计依据来自本轮此前讨论中对 DeerFlow 固定提交 `169002a4c995881ae046f34a540bb83b6ac828d6` 的只读核查：tool_receipt.py 的压缩/展示身份限制、receipt_verification.py 的引用检查范围、batch_service.py 的 advisory 验收，以及 runtime/goal.py 的继续控制。借鉴语义边界，不将该项目实现或本次文档校验计为 Hecate 运行测试。

未执行产品 pytest、typecheck、真实模型或文件/业务故障矩阵；本轮没有运行时代码变更，这些测试作为未来实施验收条款。未提交、推送、创建 PR 或归档；CI 和 PR review 未运行。

## Scope correction: defer personal workbook validation

用户明确 Problem_Lab.xlsx / P01—P08 属于 step1—step19 完成后的个人独立验证。本轮从当前演进计划删除工作簿到平台要求的覆盖表、目录/路线图迁移表、专项评测/收口待办及验收矩阵行，不仅去除标题或隐藏来源编号。平台自身的授权、恢复、Provider 替换和发布质量等要求按其已有职责保留。

step1 的已完成 SC 基线条目只去除 P 映射措辞，继续保留勾选及证据引用；原始基线、场景清单和测试文件均未修改。文档审计对该明确措辞修订做精确归一化，其他历史已完成行及 step6a—6f 原交付行仍逐字核对。后续个人验证不作为本方案实施、目录同步或发布门禁的前置。

本轮实际通过 OpenSpec strict、文档链接/表格/围栏/历史记录审计、工作簿残留检查和 git diff --check。代码与清单元数据未变，沿用前轮静态检查结果；未新增或宣称运行产品测试、CI 或 PR review，未提交或推送。

## Scope correction: target positioning

用户确认以执行核心而非功能枚举表达 Hecate 的目标定位。主方案现将 Hecate 定义为企业级 Agent 原生、持久化、强治理智能执行平台：内置且可独立交付的 Runtime 承担自主 Agent、确定性 Workflow 及组合执行，平台在其上提供可靠恢复、可信动作、版本发布、人工干预与协作治理。

独立集成和受管运行是两种交付方式。业务 App 可以直接集成执行组件；需要集中治理时，纳入控制面的是执行宿主、Deployment 与 Run，而非业务 App 本身。开发、发布和协作继续保留为平台能力，但不再与核心架构差异并列堆叠。正式 `docs/design/positioning.md` 仍按主方案 step2 的规范迁移任务同步，本 change 不提前改写当前产品事实。

本轮复跑 OpenSpec strict、可重复文档审计、定位措辞检查和 `git diff --check`。没有运行时代码变更，未新增产品测试声明；未提交或推送。
