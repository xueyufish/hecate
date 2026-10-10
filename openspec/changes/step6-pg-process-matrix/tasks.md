# Tasks

## 1. CI job 接线

- [x] 1.1 `.github/workflows/ci.yml` 新增 `step6-pg-process-matrix` job:postgres:16 服务容器、`HECATE_STEP6_POSTGRES_URL`、uv sync(同款包列表)、`uv pip install --no-deps -e packages/hecate-runner`、驱动依赖安装、运行三个受管进程场景文件(SC04 + SC05 唤醒链 + SC05 重连,双参数自动展开)。验证:workflow YAML 可解析;本地真实 PG 预跑 28 passed 全绿(2026-10-10,Docker postgres:16 @127.0.0.1)。实施发现:共享 PG 库上多测试互相看见 durable 行(重连场景的 list_tasks 断言 14≠1)——`step6_runner_database_url` fixture 已补每测试独立 schema(search_path options + CREATE/DROP,DDL 经线程池避开 loop-thread psycopg 挂起);本地预跑的其余失败为修复前 public 残留表污染(checkfirst 经 search_path 回退跳过建表),清库复跑即绿,CI 全新库不受影响。此前归档的 Windows 10013 子进程限制在本机未复现(127.0.0.1 直连)。

## 2. CI 绿灯确认与文档翻转(绿灯为前置)

- [ ] 2.1 推送后确认 `step6-pg-process-matrix` job 绿灯(含 `[postgres]` 参数真实执行,非 skip)。验证:CI run 日志显示 postgres 参数用例执行并通过。
- [ ] 2.2 演进方案 step6f 条目按绿灯证据追加修正(宿主故障矩阵/未决外部写/迟到回执的 PG 认证交付;SC03/SC06 部分覆盖与 step7/10 策略项保留);`docs/refactor/step6-followup-review.md` 追加验收记录;场景清单 SC04/SC05 的 gaps 更新(PG 矩阵常态化)。验证:方案文本与 CI 证据一致。

## 3. 验证

- [ ] 3.1 门禁:`ruff check src/ tests/ packages/`、`ruff format --check`、`mypy src/ packages/` 全绿(本 change 无生产代码改动,门禁覆盖 workflow YAML 的变更);受影响面本地回归(tests/test_execution/ 场景相关);OpenSpec strict 校验通过;CI 全绿(含新 job)。
