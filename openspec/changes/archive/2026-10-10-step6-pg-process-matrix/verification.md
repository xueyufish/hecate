# step6-pg-process-matrix — Verification

> 状态:CI 接线交付并合入(PR #241,merge queue 全绿,含本 job 首跑);
> 文档翻转随本 PR 收尾。SC03/SC06(standalone 宿主)保持 SQLite 部分覆盖,
> step7/10 策略项(中心缓冲/补传限额、只读继续)不在本 change。

## Evidence

- **CI**:`step6-pg-process-matrix` job 随 PR #241 的 merge queue 全绿合入
  (squash `d08100e`)——postgres:16 服务容器 + `HECATE_STEP6_POSTGRES_URL`,
  三个受管进程场景文件(SC04 租约续停、SC05 唤醒链、SC05 重连)以
  `[sqlite, postgres]` 双参数执行,宿主数据库、动作账本、命令与等待记录
  落在真实 PostgreSQL。
- **本地预跑**(Docker postgres:16 @127.0.0.1:5433):修复 fixture 后
  28 passed 双参数全绿。预跑暴露并修复一个真实缺陷:共享 PG 库上所有测试
  互相看见 durable 行(重连场景 `list_tasks` 断言 14≠1)——
  `step6_runner_database_url` fixture 改为每测试独立 schema(search_path
  URL options + CREATE/DROP DDL,经线程池执行避开已归档的 loop-thread
  psycopg 挂起)。修复后的残余失败经排查为更早一次未隔离预跑在 public
  残留的表经 search_path 回退污染(checkfirst 跳过建表),清库复跑即绿;
  CI 的全新数据库不经过该弯路。
- **同 PR 携带**:`test_reports_reconciliation.py` 与
  `test_evaluation_annotations_api.py` 的种子从固定日期(2026-09-10)改为
  锚定当前时间回退 4 天——报表窗口"截至现在的最近 30 天"使固定日期在第
  30 天滑出窗口(pair_count 归零、override 消失、llm_judge 分组缺失),
  CI 首跑即爆;更广评测回归 302 passed。
- **门禁**:workflow YAML 可解析;ruff/format 全绿;`mypy src/ packages/`
  893 文件零错;OpenSpec strict 校验通过。
- **注记**:已归档的 Windows 限制(Runner 子进程连 Docker PG 被拒,
  10013)在本机经 127.0.0.1 直连未复现;该认证以 Linux CI 为准的结论
  不变。

## Honest scope

- SC03/SC06(standalone 宿主)未做 PG 参数化,按原记录保持部分覆盖。
- 中心上传缓冲/补传限额、证据不可写时只读继续的策略归 step7/10。
- 矩阵 job 是否纳入分支保护必需检查由仓库设置决定;其常态执行自此 PR 起。
- Step6 至此收敛:6a–6f 关闭门槛全部交付,剩余项逐条列 Step7/10 依赖
  (合法审批判定、工具级动作范围、撤权与断连窗口策略、中心缓冲限额、
  SC03/SC06 整场景认证)。
