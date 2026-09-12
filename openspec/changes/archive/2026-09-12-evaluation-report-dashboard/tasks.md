## 1. 后端聚合 service

- [x] 1.1 创建 `src/hecate/ops/evaluation/reports/__init__.py` 与 `service.py`：窗口/分桶参数解析（默认 30 天、clamp 365 天、hour ≤ 30 天、UTC 分桶）
- [x] 1.2 实现 overview 聚合：四卡（offline pass_rate 均值、online 均分、runs+scored 计数、活跃 dataset/中位 items/低样本 run 占比（阈值 20）、error 率 `-1.0` 占比），空数据返回零值 payload
- [x] 1.3 实现 trends 聚合：dimension ∈ dataset|workflow|agent、bucket ∈ day|hour；offline 走 completed runs 的 `summary.pass_rate`，online 按 metric 聚 `evaluation_task_scores`
- [x] 1.4 实现 distributions 聚合：按 `run_id`/`task_id` scope，10 桶 `[0,1]` Python 分桶，`-1.0` 排除进 `error_count`
- [x] 1.5 实现 breakdowns 聚合：group_by ∈ agent|task|session|source，per-group per-metric avg + count，`metric_name` 过滤
- [x] 1.6 实现 session rollup：按 session 聚 per-metric avg、trace_count、agent 归属、last-scored 排序分页
- [x] 1.7 定义各端点 Pydantic 响应 schema（同模块内）

## 2. 后端 API 端点

- [x] 2.1 创建 `src/hecate/ops/api/evaluation_reports.py`：五个 `GET /evaluation/reports/*` 端点，`get_auth_context` 鉴权，422 校验（dimension/bucket 非法值）
- [x] 2.2 在 `main.py` 注册 router，确认 OpenAPI 文档生成正常

## 3. 后端测试

- [x] 3.1 `tests/test_api/test_evaluation_reports_api.py`：overview 四卡数值断言（含混合数据与空 workspace 场景）
- [x] 3.2 覆盖度低样本比例断言（1/3 = 0.3333 场景）、workspace 隔离断言
- [x] 3.3 trends：同日两 run 合并桶均值 0.75 场景、非法 dimension 422、hour/day 分桶
- [x] 3.4 distributions：error 值排除进 `error_count`、bin 边界；breakdowns：source/agent 分组
- [x] 3.5 session rollup：多 trace 聚合 avg=0.6/trace_count=3 场景、排序与分页

## 4. 前端评估页

- [x] 4.1 `lib/api-client.ts` 增加 reports 端点类型与调用函数（五个端点 + 复用 compare）
- [x] 4.2 创建 `web/src/app/(dashboard)/ops-center/evaluation/page.tsx`：tab 框架（Overview / Online / Runs / Compare）+ 窗口选择器 + 空态
- [x] 4.3 Overview 视图：四卡（质量/规模/覆盖度/错误率，tooltip 注明口径）+ Recharts 趋势图
- [x] 4.4 Online 视图：采样预算头部条（task metrics vs 配置上限）+ agent × metric 图 + session 列表下钻
- [x] 4.5 Runs 视图：run 选择器 + per-metric 直方图 + 低分列表（行展开 reasoning、source 分色徽标）
- [x] 4.6 Compare 视图：双 run 选择 + per-metric delta、token/latency/cost 配对 delta、drift 徽标、evaluator 口径提示文案
- [x] 4.7 侧边栏 Ops Center 增加 Evaluation 导航项（lucide `ClipboardCheck`，链接 `/ops-center/evaluation`）

## 5. 前端测试

- [x] 5.1 vitest：四卡渲染、空态文案、低分行展开 reasoning、compare delta 渲染

## 6. 验证

- [x] 6.1 `ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/` 全绿
- [x] 6.2 `python -m pytest tests/ -q` 全量通过
- [x] 6.3 `cd web && npm run lint && npm run test` 通过

## 7. Archive 前置（/opsx-archive 时执行）

- [x] 7.1 roadmap.md 7.2e 条目标记完成并追加延后项去向（7.2d/7.2f/OE3/v1.5）
- [x] 7.2 feature-catalog.md 7.2e 条目按 7.2c 格式补 Shipped 说明与 Deferred 清单（引用 proposal Non-goals 表）
