# Proposal

## Why

`openspec validate --specs` 在 main(`221e233`)上有 86 个失败条目(112 passed / 86 failed / 198 items)。失败全部为结构卫生问题,不是行为缺陷:83 个 spec 缺 `## Purpose` 章节(多为归档同步时只搬了 delta 正文、未补 Purpose),2 个 spec 残留 `## ADDED/MODIFIED Requirements` delta 头导致缺 `## Requirements` 主章节(dataset-synthesis、mcp-gateway),3 个 spec(injection-detection、output-findings-wiring、prompt-leakage-protection)共 17 条 Requirement 没有任何 Scenario。校验失败使 `openspec validate --specs` 无法作为质量门禁使用。

本 change 直接修复主 spec 文件的结构,不改变任何 Requirement 的行为语义(不新增/修改/删除任何 Requirement 陈述,只补缺失的 Purpose 文本、规范化章节头、为既有 SHALL 陈述补写其隐含场景)。

## What Changes

**实施中范围补充**(首次全量校验后新发现,同属结构卫生、无语义变更):28 个更早归档的 spec 存在 `TBD - created by archiving change ...` 占位 Purpose(validator WARNING),已全部替换为实际撰写的 Purpose;session-memory 因使用 `### REQ-N:` 语法在结构修复后暴露 6 条缺 Scenario 的 Requirement,已补齐;prompt-leakage-protection 一条 Requirement 原文缺 SHALL 助动词,已做等义改写(不改行为语义)。最终实际计:114 个文件修改、24 条场景补齐、109 处 Purpose 撰写或替换。

- **77 个 spec 补 `## Purpose`**:70 个以 `## ADDED/MODIFIED Requirements` 开头的文件在章节头前插入 Purpose(一句英文,从该 spec 的 Requirement 集合推写);7 个仅有标题的文件(broadcast-pipeline、mcp-server、sequential-pipeline、skill-loader、agent-workflow-embedding、signed-agent-cards、unified-skill-registry)同上;`action-authorization` 的既有导语本身就是 Purpose 文本,仅补章节头。
- **delta 头规范化**:把每个文件的首个 `## ADDED/MODIFIED Requirements` 改名为 `## Requirements`,删除后续重复的 delta 头;7 个同时含 ADDED+MODIFIED 的文件(agent-invocation、evaluation-dataset、evaluation-framework、kb-hit-testing、graph-dsl、context-assembler、evaluation-api)按 Requirement 名去重(同名的保留 MODIFIED/靠后的块)。无 REMOVED 残留(已核实)。
- **3 个文件的 `## Overview` 改名为 `## Purpose`**(knowledge-memory、memory-isolation、session-memory)——其 Overview 文本即用途陈述。
- **2 个已有 `## Purpose` 的文件仅改 delta 头**(dataset-synthesis、mcp-gateway)。
- **17 条 Requirement 补 Scenario**:injection-detection(7 条)、output-findings-wiring(4 条)、prompt-leakage-protection(6 条);场景从对应 SHALL 陈述直接导出,WHEN/THEN 格式。

## Capabilities

(无 —— 本 change 不引入、不修改任何能力契约的语义;`skip_specs: true`。被修改的是 spec 文件自身的结构完整性。)

## Impact

- **修改范围**:`openspec/specs/` 下 86 个 spec 文件;无源代码、无测试、无行为变更。
- **验证**:修复后 `openspec validate --specs` 达到 0 失败;逐文件 diff 仅含 Purpose 插入、章节头改名、场景追加。
- **风险**:低 —— 纯 Markdown 结构修复;去重逻辑仅在同名 Requirement 重复时生效(保留靠后的 MODIFIED 版本),实施时逐文件核对无静默丢块。
- **不做**:不重写任何 Requirement 陈述文本;不解决 validator 的 INFO 级提示(如超长 Requirement 文本);不清理 research 文档。
