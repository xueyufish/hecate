# Tasks

## 1. 结构修复(Purpose + delta 头)

- [x] 1.1 修复脚本一次跑完失败文件:插入 Purpose(81 个:70 个 delta 头文件 + 7 个无头文件 + 1 个既有导语补头 + 3 个 Overview 改名)、delta 头规范化(首个 `ADDED/MODIFIED` → `## Requirements`,删除后续 delta 头)、为缺 `## Requirements` 头的文件补头;另将 28 个 TBD 占位 Purpose 替换为实际撰写的 Purpose(范围补充,见 proposal)。
- [x] 1.2 脚本内置不变式:每文件修复前后 Requirement 名集合一致;无 REMOVED 段(已核实为零);去重仅在同名时生效(实际无重名,7 个混合文件为纯头改名)。

## 2. 场景补齐

- [x] 2.1 injection-detection:req[3,4,5,6,7,8,11] 补 8 条 Scenario(含 req[7] 两条)。
- [x] 2.2 output-findings-wiring:req[4,7,8,9] 补 4 条 Scenario。
- [x] 2.3 prompt-leakage-protection:req[5,7,8,9,11,12] 补 6 条 Scenario。
- [x] 2.4 session-memory(实施中发现:`### REQ-N:` 语法此前不被识别,结构修复后暴露):REQ-1—REQ-6 补 6 条 Scenario。

## 3. 语义中性微修

- [x] 3.1 prompt-leakage-protection req[7]:原文 "Identical to injection-detection: runs inside..." 缺 SHALL 助动词,改写为含 SHALL 的等义陈述(validator RFC-2119 警告);不改行为语义。

## 4. 验证与收尾

- [x] 4.1 `openspec validate --specs`:198 passed / 0 failed / 0 WARNING,exit 0;仅余 INFO 级提示(超长 Requirement 文本,按 proposal 明确不做)。
- [x] 4.2 抽查 diff:仅含 Purpose 插入、章节头改名/删除、场景追加、TBD 替换;Requirement 名集合逐文件守恒(脚本断言)。
- [x] 4.3 提交 commit;推送与归档按用户指示执行。
