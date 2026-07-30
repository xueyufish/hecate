## Why — 动机

Hecate 前端代码库在 26 个文件中包含约 236 个硬编码的中文字符串。随着项目准备在 P2 后公开到 GitHub，所有代码必须使用英语，以与最近翻译的文档保持一致，维护专业、一致的代码库。

## What Changes — 变更内容

- 将 `web/src/` 中所有中文 UI 文本翻译成英语（标签、占位符、提示、状态消息、节点类型名称）
- 修复"handoff"边标签不一致问题：将 `"移交"` 改为 `"Handoff"`，涉及 `dsl-bridge.ts` 和 `canvas-area.tsx`，并更新所有相等性检查
- 修复拼写错误：`"knowledge-rerieval"` → `"knowledge-retrieval"`（在 `config-panel.tsx` 中）
- 更新 `dsl-bridge.test.ts` 中的断言以匹配翻译后的字符串
- 将 `node-palette.tsx` 中的中文类型标签翻译成英语
- 翻译所有 `alert()` 消息（验证成功/失败、错误）

## Capabilities — 能力变更

### 新增能力

（无——这是一个翻译/重构变更，没有新能力）

### 修改的能力

（无——没有规范级别的行为变更，仅 UI 文本翻译）

## Impact — 影响范围

- **前端**: `web/src/` 中所有 26 个包含中文字符串的文件（组件、页面、库、测试）
- **测试**: `dsl-bridge.test.ts` 包含必须更新的中文断言
- **无后端/API 变更**: 仅影响前端字符串字面量
- **无破坏性变更**: 功能保持相同，仅显示文本更改