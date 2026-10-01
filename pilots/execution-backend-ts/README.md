# execution-backend-pilot-ts

非 Python 试点后端,验证 Hecate 执行契约(`execution-backend-contract`,**0.x 草案,未冻结**)可被零 Hecate Python 依赖地实现与验证。

**性质声明:** 这是验证资产,不是产品包。不进 uv workspace、不随产品发布、不承诺生产可用;实现范围是窄切片(能力发现/幂等提交/确定性 Run 事件流/取消两态/无副作用 echo 工具/最小身份校验),仅绑定 loopback、无生产凭据、无受保护写入。契约冻结条件由演进方案 step8 决定;冻结前本实现的 wire 行为可能随契约修订(附理由)而变化。

**依赖面:** 运行时仅 `node:http` + `ajv`;构建 `tsc`;测试 `vitest`。实现与验证过程不调用任何 Hecate Python 代码。

**运行:**

```bash
npm install
npm run build
node dist/server.js --port 0        # 随机 loopback 端口;--port N 固定
npm test                            # 构建 + A 侧自证(样本×schema 校验 + wire 硬语义断言)
```

**复现(A 侧证据):** 见 `docs/refactor/execution-backend-pilot-report.md`(B 侧 Python live 参数证据同文件分栏)。

**契约引用:** schema 与标准样本按相对路径直接引用仓库源文件(`src/hecate/contracts/schemas/`、`tests/test_execution/samples/`),不复制——契约修订必须落在源头。
