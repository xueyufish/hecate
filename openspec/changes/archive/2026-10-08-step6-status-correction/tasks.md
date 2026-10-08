# Tasks

- [x] 1. 修正演进方案 step6c 条目:补记受管命令下发、宿主处理、效果回执与新 attempt 关联代码已随 #232 交付(`managed_channel.command_for_host`、runner `_apply_command`/effect 上传);剩余改为"真实受管进程全链验收(等待→命令→唤醒→新 attempt→终态、两端重启、确认丢失、重复/过期命令)与 step7 合法审批判定"。
- [x] 2. 归档 `2026-10-08-complete-step6-recovery-gates/tasks.md`:在 Task2 与 Task3 行下各追加一行"更正(2026-10-08)"注记,说明原生 continuation 未实现、生产父子 adapter 不存在及测试证据形态;不改写原有勾选与文字。
- [x] 3. `docs/refactor/step6-followup-review.md` 追加"2026-10-08 状态记录修正"小节:两处超额声明的代码证据、step6c 文本滞后说明、五个后续收口 change 拆分与依赖关系。
- [x] 4. 验证:`openspec validate --strict` 通过;核对三处文档 diff 仅限登记范围;引用的代码位置(`task_dispatcher.py`、`builtin.py`、`task_control.py`、`managed_channel.py`、`hecate_runner/managed.py`)在 main 可解析。
