# lease-renewal-policy — Verification

> 状态:实现与本地验收全部交付;CI 随分支首次推送/合并(PR)运行标准套件,
> 绿灯即为本记录的形式确认。6b 的 step7 依赖行(工具级动作范围、合法审批、
> 撤权及断连窗口的策略认证)不在本 change,场景保持 planned。

## Evidence(本地)

- 进程级验收(SC04 技术切片,`tests/scenarios/test_sc04_lease_renewal_policy.py`,
  安装 wheel + 真实平台 HTTP,4/4 绿):
  1. `test_sc04_multi_action_run_renews_lease_per_action` — 同 Run 双受保护写
     逐一续租,业务调用计数逐一核对(quantity 3/7 各恰好一次、read 一次),
     nonce 记录 ≥2 条;
  2. `test_sc04_stopped_lease_delivery_refuses_within_declared_window` —
     首次 accept 后停发 pull(中间件对 `/managed/host/pull` 返回 503),第二
     受保护动作在声明预算(0.6 s)内显式拒绝,Run 失败、拒绝后零业务调用、
     前序动作事实不变、失败终态仍上传投影;
  3. `test_sc04_scope_violation_refuses_without_waiting_or_business_call` —
     runner 配置放行 domain_b、租约 scope 仅 domain_a,拒绝来自租约层且零
     业务调用;
  4. `test_sc04_host_restart_keeps_renewal_healthy` — 重启后 nonce 记录延续
     (≥2 条 → ≥4 条),新租约正常武装,持久化不阻断合法续租。
- 闸门级单测(packages/hecate-runner/tests/test_lease_gate.py):拒装保留当前
  租约(篡改/过期/异体/同 nonce 回注)、跨重启回注拒绝、过期条目载入清理、
  坏行跳过、写入失败保守拒绝授权;引擎级有界等待/续租/越界立即拒绝
  (test_engine.py);profile 窗口默认值/覆盖/非正数拒绝(test_managed_profile.py)。
- 回归:`packages/hecate-runner/tests/` 全套 + SC04 + SC05 零修改回归
  = 166 passed;`mypy src/ packages/` 892 文件零错;ruff check/format 全绿;
  OpenSpec strict 校验通过。

## Gates

```bash
ruff check src/hecate/ packages/hecate-runner/ tests/scenarios/test_sc04_lease_renewal_policy.py
ruff format --check packages/hecate-runner/ tests/scenarios/test_sc04_lease_renewal_policy.py
mypy src/ packages/    # Success: no issues found in 892 source files
pytest packages/hecate-runner/tests/ tests/scenarios/test_sc04_lease_renewal_policy.py \
       tests/scenarios/test_sc05_managed_wake_chain.py -q   # 166 passed
openspec validate lease-renewal-policy --strict            # valid
```

## Honest scope

- 6b 技术闭环完成;租约格式、签发语义与 pull 协议零修改(平台侧零代码变更)。
- 租约过期对真实时钟偏移的认证归 SC04/step7(平台签发 TTL 固定 120 s,过期
  拒绝路径由闸门级单测与断连变体覆盖)。
- 平台事件日志追加的 fencing 不在本 change(6d 边界行的指向已更正:归 6f
  矩阵与 step7 撤权窗口)。
- 工具级动作范围、合法审批判定、撤权流程归 step7;场景整体保持 planned。
- Step6 总体保持部分完成(6e/6f 及 step7 依赖未关)。

## 2026-10-10 CI 首跑更正(重放窗口语义收窄)

CI 主套件暴露:`update` 的宽验证(拒装异体/过期租约并保留当前租约)改变了
既有安全契约——平台侧测试(`test_managed_action_authorization`)注入异体
租约后期望派发边界以精确原因拒绝、业务写零调用,而宽验证下当前合法租约
仍武装、写调用发生。更正:`update` 仅在安装前拒绝**已消费 nonce 的重放
租约**(重放窗口目标不变),其余无效租约恢复既有语义——照常安装、由派发
边界以精确原因拒绝并留证据。CI 的 4 个失败测试零修改回绿;主 spec 同步
更正;回归面扩大到 `tests/test_execution/` 全目录。
