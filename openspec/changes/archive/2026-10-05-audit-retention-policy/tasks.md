# Tasks

## 1. 策略面与 JSONL 后端

- [x] 1.1 `runner.json` 新增可选 `evidence` 配置块(`dir`/`backend`/`retention_days`/`capacity_limit`),校验与错误信息;未配置块 = 显式无策略;`evidence_dir` 旧位置保持兼容,块内 `dir` 覆盖。验证:profile 解析正/负用例(非法类型、负数、未知 backend)。
- [x] 1.2 `EvidenceStore` 留存清理:append 后惰性删除超过 `retention_days` 的天文件(按文件名日期,不解析内容;清理失败仅记录不阻塞);清理动作留痕。验证:注入时钟/旧文件,过期文件被删且查询不再返回。
- [x] 1.3 容量检查与门语义:probe 先算用量,超限先触发清理,复检仍超限抛 `EvidenceCapacityError`(OSError 子类,携带类别);readonly 不受影响。验证:超限注入测试——受保护动作拒绝带超容量类别,read 继续执行。
- [x] 1.4 失败类别显式化:门拒绝记录携带类别(不可写/超容量);最近失败类别与时间可经健康/查询面读取,写入失败时以最近成功记录+内存态为准,不伪造。验证:两类失败各自的拒绝记录断言。

## 2. SQL 委托后端

- [x] 2.1 SQL 证据后端:宿主本地库 `evidence_records` 表(ts/kind/principal/ref/outcome/detail),`create_schema` 自建,append/query/probe 与 JSONL 同契约;容量按行数、留存按 ts 年龄。验证:CRUD/过滤/probe 单测(SQLite)。
- [x] 2.2 `backend: "durable"` 装配:durable profile 时可委托,复用 `DurableConfig.database_url` 同库;非 durable profile 配置 durable 后端启动报错(fail-fast);不触平台表与 alembic。验证:装配正/负用例。
- [x] 2.3 `EVIDENCE_IMPLEMENTATIONS` 参数化套件:两后端同跑 append/query/过滤/probe/容量拒绝/留存过期六类断言(模式照 `DURABLE_IMPLEMENTATIONS`)。验证:参数化测试双后端全绿。

## 3. 可观测与文档收尾

- [x] 3.1 能力摘要与健康端点:报告证据后端、策略(留存期/容量/缺省)、当前用量、最近门失败;`GET /healthz` 携带。验证:健康响应断言(有策略/缺省两态)。
- [x] 3.2 文档与方案同步:`packages/hecate-runner/README.md` 证据留存段(配置示例、单位声明、失败类别);演进方案 step6 末项(:525)勾选并注记交付;SC06 保持 `planned`(中心半边归 step10)。验证:文档与交付一致;manifest 未动。
- [x] 3.3 回归与门禁:既有 runner 测试全绿(无 `evidence` 块行为不变,回归红线);`ruff check/format`、`mypy src/ packages/`、scoped pytest(runner 包 + scenarios)零错误。验证:本地门禁零错误。
