## Context

Hecate frontend (`web/src/`) has 236 Chinese string literals across 26 files. Documentation was recently translated to English (CN→EN). The codebase needs the same treatment before going public. All changes are UI text only — no behavior changes.

Current state: Chinese strings exist in UI components (labels, placeholders, buttons), alert messages, status indicators, node type names, and test assertions.

## Goals / Non-Goals

**Goals:**
- Translate all Chinese UI text to English
- Maintain consistency across all files (same English term for same Chinese term)
- Fix the "handoff" edge label inconsistency (`"移交"` → `"Handoff"`)
- Fix the `config-panel.tsx` typo (`"knowledge-rerieval"` → `"knowledge-retrieval"`)
- Update test assertions to match translated strings

**Non-Goals:**
- Introducing i18n framework (deferred to P3/P4)
- Changing any functionality
- Translating documentation (already done)
- Translating comments or variable names (they're already English)

## Decisions

### D1: Direct string replacement, not i18n framework

**Choice**: Replace Chinese strings directly with English equivalents in the source code.

**Alternatives considered**: Introduce `next-intl` or `react-i18next` with translation keys.

**Rationale**: i18n is a P3/P4 concern per project decision. Direct replacement is simpler, has zero risk of breaking functionality, and achieves the immediate goal (English-only codebase for public repo).

### D2: Consistent terminology mapping

**Choice**: Use a fixed mapping of Chinese → English terms across all files:

| Chinese | English | Used In |
|---------|---------|---------|
| 对话 | Conversation | nodes, UI labels |
| 条件 | Condition | nodes, UI labels |
| 工具调用 | Tool Call | nodes, UI labels |
| 知识检索 | Knowledge Retrieval | nodes, UI labels |
| 变量设置 | Variable Set | nodes, UI labels |
| 移交 | Handoff | edge labels, logic checks |
| 开始 | Start | start node |
| 结束 | End | end node |
| 工具模式 | Tool Mode | agent node |
| 保存 | Save | toolbar button |
| 验证 | Validate | toolbar button |
| 测试运行 | Test Run | toolbar button |
| 编排模板 | Templates | toolbar button |
| 输入 | Input | toolbar button |
| 历史 | History | toolbar button |

### D3: Translation execution order

**Choice**: Translate in two batches:

1. **Workflow canvas files** (core, ~120 occurrences): `workflow/*`, `dsl-bridge.ts`, `workflows/*`
2. **Other pages** (~116 occurrences): agents, knowledge, models, login, sidebar

**Rationale**: Workflow canvas is the most complex (edge labels used in logic), needs careful attention first.

### D4: Edge label logic fix

**Choice**: When translating `"移交"` to `"Handoff"`, update all equality checks in `canvas-area.tsx` and `dsl-bridge.ts` simultaneously.

**Risk**: Missed reference → broken handoff edge rendering.
**Mitigation**: Search for all occurrences of `"移交"` before and after translation.

## Risks / Trade-offs

- **[Missed translation]** → Search for Chinese characters after each batch to verify completeness
- **[Test failure]** → Run `npm test` in `web/` after each batch to catch assertion mismatches
- **[Logic break from edge label change]** → Verify `"Handoff"` is used consistently in all equality checks
- **[Inconsistent terminology]** → Use the fixed mapping table (D2) for all translations
