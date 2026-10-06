# Spec Delta

## ADDED Requirements

### Requirement: 受管运行派发入口接入租约门且与打点身份同源

受管运行(经理由受管调度驱动的任务)SHALL 在宿主内标记来源;其保护动作派发 SHALL 先经租约门(签名、期限、部署绑定、nonce 消费)再进入动作意图/领取。租约的签发者与部署绑定 SHALL 与受管身份打点同源(同一受管信任根派生),跨宿主或跨部署绑定的租约 MUST NOT 驱动本宿主的受管动作。standalone(非受管)运行的派发路径 MUST 保持不变——本地信任材料语义与租约门无关。

#### Scenario: 受管保护派发先过租约门

- **WHEN** 受管运行派发保护动作
- **THEN** 租约门先于动作意图/领取验证当前租约,通过后才进入既有台账与业务派发路径

#### Scenario: 跨部署绑定的租约不驱动本宿主动作

- **WHEN** 一个绑定其他部署域的租约出现在本宿主租约门
- **THEN** 验证拒绝(audience 不匹配),保护动作不派发

#### Scenario: standalone 派发路径不受影响

- **WHEN** 非 control_plane 的 standalone 宿主执行保护动作
- **THEN** 派发路径与既有语义完全一致,无租约验证参与
