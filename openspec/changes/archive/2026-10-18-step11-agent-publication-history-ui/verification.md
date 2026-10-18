# 验证记录 — step11-agent-publication-history-ui

## 本次审查修正

- Agent 变更时原面板继承旧 offset 与展开状态，可能直接请求新 Agent 的后续页。现通过按 agentId 设置 key 的内层组件同步隔离分页、展开、请求与已知总数。
- 后续页查询失败后原实现隐藏分页条，用户只能反复重试。现保留最后成功查询的总数与导航，允许返回上一页；初次加载尚无总数时不虚构计数。
- 报告缺失 bypassed_by_force（包括合法的 gate_off 最小报告）曾被当成 false 显示“否”。现区分 true / false / 未记录。
- gate_config 为 null 时曾只有孤立的“未记录”，没有字段标签。现保留门禁配置标签与缺失标注。
- Run ID 可空时曾同时隐藏其哈希。现将 candidate/baseline 的 ID、哈希分别展示，忠实呈现每一个已记录字段。
- 未知 outcome 若等于 constructor / __proto__ / toString，原字典访问会返回原型属性。现仅匹配 map 自有属性，其余保持原始字符串和默认样式。
- 原设计错误地把 A→B→A 的正确性归于身份标签，并认为 effect active flag 不足。已明确标签负责隐藏不匹配数据、每次请求的 cleanup 负责阻止旧响应；补充真实 A→B→A 测试。

以上修复仅涉及本 change 的前端实现、测试与 OpenSpec 工件；未修改全局 API client、VersionPanel、Task 页面或任何后端文件。

## 实际执行结果

| 检查 | 本次审查结果 |
|---|---|
| 修复前回归测试 | 新增 9 个用例，其中 8 个复现失败，A→B→A 保护已有正确实现 |
| 本 change 测试 | publication-history.test.tsx：26 passed |
| 完整前端测试 | npm test -- --reporter=dot：15 个文件、131 passed |
| ESLint | npm run lint：exit 0，0 errors，2 条既有 warning |
| TypeScript | npx tsc --noEmit：exit 0，0 errors |
| Production build | npm run build：exit 0，28/28 静态页生成，/agents/[id] 正常构建 |
| 仓库统一检查 | .venv/Scripts/python.exe scripts/dev.py check all：exit 0；ruff check 通过，format 1601 个文件通过，mypy 检查 950 个源文件无错误 |
| OpenSpec | openspec validate step11-agent-publication-history-ui --strict：通过 |
| 空白检查 | git diff --check：通过 |

新增测试包含 Agent 分页重置、缺失 bypass 不推断 false、空配置保留标签、可空 ID 不隐藏哈希、后续页失败可返回、原型属性 outcome 回退、实际 A→B→A 迟到响应。

两条 ESLint warning 来自 knowledge/[id] 页 loadDocs 和 settings/models 页 fetchProviders 的 effect 依赖。全量测试另有 ModelsPage 列表 key warning。已确认这些来源文件与 HEAD 一致，本 change 未修改；相关警告也在此前 step14 审查中记录过。本 change 测试无失败。

## 生产构建浏览器走查

本次使用本 worktree 的 next build 产物，以 next start 启动，配合仅监听本机的 Node 契约桩代理进行浏览器验证；没有连接真实平台数据库、评测服务或模型。

实际通过正常浏览器点击完成：

1. 桩账号登录 → Agent 列表 → Agent 详情，发布历史位于版本面板与渠道管理之间。
2. 同一版本四条凭据分别呈现“门禁通过 / 告警模式放行 / 强制放行（绕过门禁） / 门禁未启用”，没有合并为统一通过状态。
3. 展开 gate_off 凭据，candidate/baseline ID 与哈希、门禁配置均明确标注未记录；缺失 bypass 字段显示未记录。
4. 展开 passed 凭据，版本配置哈希、candidate 评测 Run 与哈希、冻结配置、通过率与信号均来自凭据。
5. 展开原始报告，完整快照可查看；点击刷新后凭据行、结论与当前行展开保持正确。
6. 截图确认面板和证据区可读，长哈希及原始快照有对应的滚动展示。

分页、错误重试、竞态和空历史的边界由自动测试覆盖，未将其写成浏览器手工验证结果。

## 保留限制

- 未执行真实平台后端联调或 CI；契约桩走查不能替代这些验证。
- 当前报告摘要展示已知字段，其他报告字段可通过原始报告快照查看。
- 旧请求 cleanup 阻止过期结果写入 UI，不中止已发出的网络请求。
- 本 change 只读，不增加发布、回滚、门禁重算或自动轮询。
- 所有修改留在原 worktree，未提交、未推送。
