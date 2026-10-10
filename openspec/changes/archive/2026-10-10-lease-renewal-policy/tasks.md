# Tasks

## 1. 续租策略声明化

- [x] 1.1 profile/装配新增 `lease_refresh_wait_seconds` 声明(默认 5.0,与现值一致),引擎 `_lease_refusal` 改读注入参数并移除模块常量依赖;README 受管运行章节写明断连/续租窗口语义与上限建议。验证:profile 单测断言默认值与注入生效;引擎单测以短预算验证超窗拒绝路径。

## 2. 重放窗口收口

- [x] 2.1 `LeaseGate.update` 安装前验证:复用 `verify_lease` + 身份/部署绑定检查;结构无效、已过期、nonce 已消费的租约拒绝安装且不替换当前租约,拒绝留证据。验证:`LeaseGate` 单测——过期回注、异体签名回注、同 nonce 回注均被拒,好租约不受影响。
- [x] 2.2 nonce 消费记录持久化:宿主本地状态目录 append-only JSONL(nonce 摘要 + 过期时间),`LeaseGate` 初始化载入、过期条目载入时丢弃;文件写入失败时保守拒绝新受保护动作(不可写即不可授权)。验证:单测覆盖写入、载入去重、过期清理、写入失败的保守拒绝;既有受管回归零断言修改通过。

## 3. 进程级验收套件

- [x] 3.1 场景套件骨架:复用 `tests/scenarios/tools/managed_platform.py` 平台栈 + wheel 安装路径,新增多写续租场景——同 Run 依次 ≥2 个受保护动作,平台按 pull 节奏持续发租约,断言每个动作恰好一次授权、业务调用计数逐一核对、全部成功。验证:套件在本地 SQLite/文件栈跑通。
- [x] 3.2 停发租约场景:平台停止 pull,在途 Run 下一个受保护动作等待超预算 → 显式拒绝留证据、Run failed 收敛、拒绝后业务调用计数不再增长、前序动作事实不变。验证:套件断言通过。
- [x] 3.3 过期与重放场景:租约过期后新动作拒绝;已消费租约跨宿主重启回注被拒(真实子进程重启),重启后新 pull 的租约正常武装。验证:套件断言通过。
- [x] 3.4 范围越界立即拒绝(不等待)与已决回放跳过闸门的回归保持。验证:套件断言通过;`test_sc05_managed_wake_chain.py` 零修改通过。
- [x] 3.5 场景清单 manifest 追加 6b slices,场景整体保持 `planned`;README/能力文档引用新套件。验证:清单一致性检查通过。

## 4. 文档同步

- [x] 4.1 演进方案 step6b 条目按验收证据追加修正行(技术闭环交付,step7 依赖行保留);`docs/refactor/step6-followup-review.md` 追加验收记录。验证:方案文本与运行证据一致。

## 5. 验证

- [x] 5.1 门禁:`ruff check src/ tests/ packages/`、`ruff format --check`、`mypy src/ packages/` 全绿;受影响单测(worker/managed/engine)与场景套件通过;CI 全绿。(本地门禁全绿:166 passed 含 SC04×4 + SC05 零修改回归;CI 随分支首次推送/合并确认,证据见 verification.md)
