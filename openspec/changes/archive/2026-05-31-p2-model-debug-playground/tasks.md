## 1. 前端：流式响应支持

- [x] 1.1 重写 `handleTest()` 以使用 `api.stream("/v1/chat/completions", ...)` 而非 `api.post("/api/models/test", ...)`
- [x] 1.2 添加流式状态管理：内容逐步累积，流式进行时显示输入指示器
- [x] 1.3 添加流式开关复选框（默认启用）
- [x] 1.4 优雅处理流式错误（回退到错误显示）

## 2. 前端：系统提示词支持

- [x] 2.1 在用户提示词字段上方添加系统提示词 Textarea 字段
- [x] 2.2 在请求中包含系统消息：`{role: "system", content: systemPrompt}` 作为第一条消息
- [x] 2.3 字段为空时省略系统消息

## 3. 前端：响应时间测量

- [x] 3.1 在请求开始前记录 `Date.now()`
- [x] 3.2 当首个内容分块到达时记录 TTFT
- [x] 3.3 流式完成时记录总时间
- [x] 3.4 在结果部分显示 TTFT 和总时间（如 "TTFT: 234ms | Total: 1.2s"）

## 4. 前端：Token 用量可视化

- [x] 4.1 添加水平进度条，显示提示词 token（蓝色）和补全 token（绿色）
- [x] 4.2 显示数字标签："Prompt: 45 | Completion: 120 | Total: 165"

## 5. 前端：测试历史

- [x] 5.1 定义 TestHistoryEntry 接口：model、prompt、systemPrompt、temperature、maxTokens、response、timestamp、latency
- [x] 5.2 将测试结果保存到 localStorage（最多 10 条，FIFO 策略）
- [x] 5.3 添加"History"按钮，打开对话框/面板显示最近 10 次测试
- [x] 5.4 历史记录上的"Load"操作，可填充表单字段
- [x] 5.5 添加"Clear History"按钮，带确认提示
- [x] 5.6 历史记录中截断内容：提示词 100 字符、系统提示词 100 字符、响应 500 字符

## 6. 前端：错误显示改进

- [x] 6.1 解析 API 错误响应并显示代码和消息
- [x] 6.2 添加建议映射：AUTH 错误 → "Check API key"、NOT_FOUND → "Model not available"、429 → "Rate limited, try again later"
- [x] 6.3 网络错误时添加重试按钮

## 7. 验证

- [x] 7.1 在 `web/` 目录运行 `npm run lint` — 零错误（1 个预先存在的警告）
- [x] 7.2 在 `web/` 目录运行 `npm run build` — 零错误
