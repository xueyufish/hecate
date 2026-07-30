## 1. 前端：自定义输入表单

- [x] 1.1 在工作流编辑器中添加输入表单面板，包含字段：messages 数组（JSON 文本域）、自定义变量（键值对）
- [x] 1.2 添加"Input"按钮，切换输入表单的可见性
- [x] 1.3 在状态中存储输入数据，作为 `input_data` 传递给测试运行 API
- [x] 1.4 添加验证：messages 必须是有效的 JSON 数组

## 2. 前端：节点输出面板

- [x] 2.1 添加节点点击处理器，打开显示节点详情的侧面板
- [x] 2.2 显示 node_id、node_type、status、input、output、error_message、duration_ms
- [x] 2.3 将输出截断至 1000 字符，带"Show more"展开按钮
- [x] 2.4 对失败的节点突出显示 error_message

## 3. 前端：执行日志面板

- [x] 3.1 在画布下方添加日志面板（可折叠）
- [x] 3.2 显示每个节点的执行顺序、开始时间、结束时间、状态
- [x] 3.3 格式化为带时间戳的日志条目

## 4. 前端：节点状态徽章

- [x] 4.1 为每个节点组件添加状态徽章（pending/running/completed/failed）
- [x] 4.2 颜色编码：gray=pending、yellow=running、green=completed、red=failed
- [x] 4.3 运行完成后用测试结果状态更新节点数据
- [x] 4.4 关闭结果面板时清除徽章

## 5. 前端：运行历史

- [x] 5.1 添加 runHistory 状态（TestRunData 数组，最多 10 条）
- [x] 5.2 每次测试完成后将结果推入历史
- [x] 5.3 添加"History"按钮，显示下拉列表展示之前的运行（时间戳、状态、耗时）
- [x] 5.4 点击历史条目加载该次运行的结果

## 6. 验证

- [x] 6.1 在 `web/` 目录运行 `npm run lint` — 零错误（1 个预先存在的警告）
- [x] 6.2 在 `web/` 目录运行 `npm run build` — 零错误
