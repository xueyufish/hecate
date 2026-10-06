# Tasks

## 1. 租约携带动作范围

- [x] 1.1 `hecate-durable` credentials:`Claims` 增加可选 `scope`(数据域列表),`lease_claims` 签发、`verify_lease` 返回,HMAC 摘要覆盖 scope;缺省/空 scope 样例回归。验证:durable credentials 单测覆盖带/不带 scope 的签发与验证、摘要敏感性。
- [x] 1.2 平台:`StandaloneEnrollmentModel.managed_scope` JSON 列 + Alembic 迁移;`SetManagedOptIn` 接受并校验 scope(非空字符串、去重);`issue_lease_for` 按 enrollment 签发带 scope 租约。验证:平台迁移与 opt-in/签发测试;旧行(无 scope)默认空列表。

## 2. 动作边界接入租约门

- [x] 2.1 `engine.py`:RunState 增加 `managed` 来源标记(`resume_managed` 置位);`_dispatch_tool` 对受管保护动作在台账门之前执行 `LeaseGate.check()` + scope 判定(与打点域取交集),拒绝返回 `authorization` 结果并留证据,业务 API 不调用;readonly 与 standalone 路径不变。验证:engine 单测覆盖受管拒绝、standalone 不受影响、readonly 不经门。
- [x] 2.2 拒绝结果的证据与计数: denial 记录携带原因(无租约/过期/重放/超范围);业务派发函数调用计数为拒绝断言的基准。验证:单测断言调用计数与证据内容。

## 3. 闭环验证(平台侧集成)

- [x] 3.1 正常路径:带 scope 租约下受管保护动作正常执行一次;pull 附带租约更新门。验证:`tests/test_execution` 闭环测试通过。
- [x] 3.2 断连到期:时间推进超过 TTL 后保护动作被拒绝、业务 API 计数为零、readonly 继续;重连取得新租约后新保护动作恢复,未知结果动作经台账不重做。验证:集成测试通过。
- [x] 3.3 旧授权与跨域:nonce 重放拒绝;请求域超出租约 scope 拒绝;跨部署绑定租约被 audience 校验拒绝。验证:集成测试通过。
- [x] 3.4 重启恢复:重启后空门拒绝首个保护动作,首次成功 pull 后恢复;既有 runner/durable/execution 回归。验证:集成与回归测试通过。

## 4. 验证与文档

- [x] 4.1 runner 包内测试:LeaseGate scope 行为、profile 配置不受影响;文档(README 受管段落、演进方案 step6b 状态)与代码一致。验证:scoped pytest 与文档核对。
- [x] 4.2 运行 ruff check、ruff format --check、mypy src/ packages/;受影响 pytest 全绿。验证:本地门禁零错误。
