# Tasks

## 1. 文档骨架与基线声明

- [x] 1.1 创建 `docs/research/platform-evolution-baseline.md` 并写第 1 节基线声明:分支、提交 `b8fd926`、日期、在途 change 状态、worktree 偏离记录(见 design D5);核对 `git log --oneline -1` 与 `openspec list --json` 输出与文中记录一致
- [x] 1.2 写第 10 节旧分支归档:记录 `origin/docs/platform-evolution-plan` 与 origin/main 无差异的核实结论及指向方案文档 §二失败调查的指针;验证 `git diff origin/main origin/docs/platform-evolution-plan --stat` 输出为空

## 2. 执行入口清单(第 2 节)

- [x] 2.1 生成 `src/hecate/main.py` 全量 `include_router` 与延迟挂载清单,合并 MCP server、A2A、channel 包入口形成 working list;验证归并表中出现的每个 router 名都能在该清单找到(grep 复核)
- [x] 2.2 将入口归并为 8 类(Agent chat、工具 chat、Workflow、MCP、A2A、定时任务、IM、评估调用),逐类记录入口文件、实际执行服务、安全入口、事件存储、取消路径,每条带 `file:line`
- [x] 2.3 每类入口记录缺口与未核验项,集中列入节末缺口清单;验证不存在缺 TODO 标记的"未核验"条目

## 3. 后端能力清单(第 3 节)

- [x] 3.1 对 Runtime、Memory、Knowledge、Evaluation、Observability、Sandbox 逐类记录:契约是否存在、`openspec/specs/` 引用、内置实现位置、缺口;验证每条 spec 引用可用 `openspec show <id> --type spec` 解析

## 4. 信任与数据流拓扑(第 4 节)

- [x] 4.1 按部署形态(同进程、Docker sandbox、远程后端)绘制数据流并列出可能绕过授权或审计的直连路径与当前阻断机制;验证每条旁路路径能在第 2 节入口清单中找到对应入口行

## 5. 托管执行组合数据流(第 5 节)

- [x] 5.1 固定 harness/环境双轴登记格式,以方案 §一已核验的 OpenAI 事实为样例记录数据驻留/保留限制的登记方式;验证格式字段与方案 §一"执行组合"表一一对应

## 6. 七能力域归属清单(第 6 节)

- [x] 6.1 对七个能力域逐域列出文件/表 owner、跨包 import、直接写表违规,逐条 grep 复核;验证每条违规带文件路径,无凭记忆填写的条目

## 7. G1—G5 门槛记录(第 7 节)

- [x] 7.1 逐项记录缺口、复现指针(方案 §二 + 具体测试文件)、owner(留 `待指定`)、目标 change;对 G1/G2 复验关键断言(MCP `tool_execute` 未传服务端上下文;TOOL_RESULT 缺失时恢复分支重复执行),验证复验结论与方案 §二记录一致

## 8. P01—P08 映射(第 8 节)

- [x] 8.1 从方案 §一映射表细化为五列:平台保证、可选实现、责任 step、验收 fixture 占位、未支持项;验证每行责任 step 在方案 §四执行总表中存在

## 9. 架构评测包规格(第 9 节,切法 A)

- [x] 9.1 冻结规格:合成输入材料及 ACL、测试工具集、预期产物 schema、禁止动作清单、审批人设定、故障注入点与环境复位、确定性断言与 rubric 分工、fixture 目录结构;验证场景集覆盖正常执行、拒绝动作、等待审批、后端失联、重复提交五类
- [x] 9.2 固定规格章节编号,写明 `platform-evolution-scenario-pack` 的引用方式与规格变更规则;验证后续 change 可按编号引用且变更须回写本基线

## 10. 交叉校验与收尾

- [x] 10.1 全文按 design D1 自检(每条"六个月后有人查吗/引用可核验吗"),前瞻表述只留指向方案 step 的指针;验证随机抽查 10 处 `file:line` 引用全部解析正确
- [x] 10.2 通读 `docs/design/writing-style.md` 确认无违规(提交哈希与日期为事实记录),运行 `openspec validate` 通过后以 Conventional Commits 格式提交(pre-commit 钩子通过)
