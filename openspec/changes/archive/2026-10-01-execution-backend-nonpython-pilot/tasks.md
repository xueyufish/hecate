# Tasks

任务组 1—3 对应 PR1(TS 试点 + A 侧自证),任务组 4 对应 PR2(Python live 参数),任务组 5—6 对应 PR3(修订闭环 + 证据/收尾)。每组完成即勾选;PR1 落地前组 4 的 live 断言无法运行,但代码可先行。

## 1. TS 试点脚手架

- [x] 1.1 环境检查:确认本机 node/npm 可用版本并记录;创建 `pilots/execution-backend-ts/`(package.json 含 `engines` 钉主版本、tsconfig、README 首段声明 0.x 草稿性质与 step8 冻结条件);仓库根 `.gitignore` 增补 `pilots/*/dist` 与 `pilots/*/node_modules` 模式;验证:`npm install` 成功且 `git status` 不出现构建产物
- [x] 1.2 建立对契约源文件的引用约定:常量模块以相对路径指向 `src/hecate/contracts/schemas/` 与 `tests/test_execution/samples/`,不复制文件;验证:TS 内 grep 无 schema/样本的复制副本,删掉任一源文件后 A 侧测试失败(临时验证后还原)

## 2. 试点后端实现(node:http,无框架)

- [x] 2.1 能力发现:`GET /capabilities` 返回三轴归属、验证条目,`pause/resume/export_context` 声明 `unsupported`;验证:响应通过 ajv 对 capabilities schema 的校验
- [x] 2.2 幂等提交:`POST /runs` 同幂等键同内容返回同一回执;同键异内容返回 409 problem+json(`detail_ns.reason=idempotency_key_content_mismatch` 并指向原请求);验证:A 侧测试两态断言
- [x] 2.3 确定性 Run 与事件流:提交驱动 `run_started → tool_call(echo) → tool_result → run_completed` 状态机;事件按序号内存存储,`GET /runs/{ref}/events` 游标分页、续读、模拟缺口出显式 gap marker;验证:断线续读用例(记下游标 → 重放 → 无丢重)
- [x] 2.4 取消两态:`POST /runs/{ref}/cancel` 立即回 `REQUESTED` 回执,执行到检查点才发 `APPLIED` 事件;wire 上两态可区分;验证:A 侧断言回执态 ≠ 终态事件
- [x] 2.5 echo 工具与业务拒绝变体:白名单参数外拒绝——业务拒绝按契约失败分层表现为 tool RESULT 事件、Run 继续(不是 error 事件);验证:A 侧断言拒绝后 Run 到达 completed
- [x] 2.6 最小身份校验:security-claims 包络在场与 audience 校验;自报角色字段落 extra 且不进入任何授权判断;缺失/错 audience 返回 403 problem+json;验证:A 侧身份负例
- [x] 2.7 隔离边界:仅绑定 127.0.0.1 + 临时端口(启动参数),无凭据配置项,工具清单仅 echo 类;验证:代码审查断言 + README 声明

## 3. A 侧自证(vitest + ajv)

- [x] 3.1 样本×schema 校验套件:加载全部 47 个标准样本,按类别对相应 schema 校验(多 schema 的 HTTP 样本对按其响应/请求侧分别校验);验证:`npm test` 全绿且不出现任何 Hecate Python 调用
- [x] 3.2 wire 硬语义套件:对真实 HTTP 响应断言幂等两态、游标续读与 gap marker、取消 REQUESTED≠APPLIED、业务拒绝为 RESULT 事件、pause 请求得 501 结构化错误;验证:`npm test` 全绿
- [x] 3.3 A 侧独立性证明:自证过程零 `hecate`/workspace 包依赖(依赖清单审查 + 无 Python 子进程);验证:README 或测试输出含依赖面声明

## 4. B 侧 live 参数(Python)

- [x] 4.1 `tests/test_execution/live_http.py` transport:实现与 Stub 相同的后端接口方法面,problem+json → 契约错误类型反解;仅依赖 stdlib http 客户端;验证:文件仅存在于 tests 目录,`src/hecate/` 无对应实现
- [x] 4.2 conftest session 级 fixture:临时端口拉起 `node dist/server.js`、健康探测、终结回收;`shutil.which("node")` 为空时 skipif 并写明理由;验证:有 node 时 fixture 正常起停;临时改 PATH 模拟无 node 时用例跳过且理由可见(验证后还原)
- [x] 4.3 契约测试参数化重构:`test_backend_contract.py` 引入 backend 工厂参数(`stub` | `live`),断言体零改动;验证:`git diff` 显示断言函数体无变化,仅夹具与参数;`pytest tests/test_execution/ -q` 全绿(有 node 时 live 同跑)

## 5. 契约修订闭环

- [x] 5.1 登记试点发现(A/B 任一侧):来源侧、歧义点、修订方式三列;无发现的类别记录覆盖面;验证:发现清单进 change 工件或证据报告
- [x] 5.2 需要修订时:同步改 schema + 样本 + 受影响断言,`$id` 保持 0.x,试点专属扩展走 vendor 命名空间;验证:三方互检测试与 A 侧校验都通过;修订理由可追溯(本次无 schema 语义修订——F1/F2 均为 OpenAPI 绑定层响应定义缺口,登记于报告,留 step8 冻结时增补;契约 schema 本体零改动)

## 6. 证据与收尾

- [x] 6.1 撰写 `docs/refactor/execution-backend-pilot-report.md`:A/B 两侧运行输出、环境(node/Python 版本)、复现命令、发现与处置;明确声明窄范围验证不构成任何后端认证;验证:报告两栏齐全且命令可照跑
- [x] 6.2 勾选方案 step3 最后一项并附证据指针(试点路径、报告、两侧测试);验证:`git diff` 方案文档仅该行及括注
- [x] 6.3 全套验证:`ruff check/format`(涉及 Python 范围)、`mypy src/`(应零差异)、`pytest tests/test_execution/ -q`、`npm --prefix pilots/execution-backend-ts test`、`openspec validate execution-backend-nonpython-pilot --strict`;验证:全部 0 错误
