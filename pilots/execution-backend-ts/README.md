# execution-backend-pilot-ts

非 Python 试点后端,验证 Hecate 执行契约(`execution-backend-contract`,**0.x 草案,未冻结**)可被零 Hecate Python 依赖地实现与验证。

**性质声明:** 这是验证资产,不是产品包。不进 uv workspace、不随产品发布、不承诺生产可用;实现范围是窄切片(能力发现/幂等提交/确定性 Run 事件流/取消两态/无副作用 echo 工具/最小身份校验),仅绑定 loopback、无生产凭据、无受保护写入。契约冻结条件由演进方案 step8 决定;冻结前本实现的 wire 行为可能随契约修订(附理由)而变化。

**依赖面:** 运行时仅 `node:http` + `ajv`;构建 `tsc`;测试 `vitest`。实现与验证过程不调用任何 Hecate Python 代码。

**运行:**

```bash
npm ci
npm run build
node dist/server.js --port 0        # 随机 loopback 端口;--port N 固定
node dist/server.js --port 0 --tool-callback-url http://127.0.0.1:PORT/echo
npm test                            # 构建 + A 侧自证(样本×schema 校验 + wire 硬语义断言)
```

**复现(A 侧证据):** 见 `docs/refactor/execution-backend-pilot-report.md`(B 侧 Python live 参数证据同文件分栏)。

**契约引用:** schema 与标准样本按相对路径直接引用仓库源文件(`src/hecate/contracts/schemas/`、`tests/test_execution/samples/`),不复制——契约修订必须落在源头。

未配置回调 URL 时启动独立 loopback 工具 HTTP 接收端；URL 只能由可信启动参数指定，不能由请求体覆盖。B 侧使用独立 Python 接收端，验证实际回调、结果映射及幂等重放不会重复调用。`contracts/echo.*` 发布工具版本、副作用类别和输入/输出 schema。回调使用独立 `pilot-tool-callback` 受众的合成凭据，禁止转发入站令牌。

身份声明检查仅用于隔离试点；当前 token 不验签，不能视为生产身份认证。租户/工作负载隔离测试证明传输身份的作用域语义，不能证明身份可信。能力证据仅适用于 loopback 测试拓扑。

CI 安装锁文件依赖并执行 A/B 两侧；`HECATE_REQUIRE_LIVE_PILOT=1` 时依赖缺失必须失败。已安装依赖时每次重编译，编译失败不能跳过或复用旧 `dist`。
