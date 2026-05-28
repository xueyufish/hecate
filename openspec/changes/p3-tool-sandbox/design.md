## Context

P1 的工具执行没有沙箱隔离。P3 需要 Docker 容器沙箱来隔离工具执行风险，防止恶意或错误的工具调用影响宿主系统。

## Goals / Non-Goals

**Goals:**

1. 实现 SandboxExecutor — Docker 容器沙箱执行
2. 实现 SandboxPool — 沙箱池管理（预热/复用/回收）
3. 实现资源限制 — CPU/内存/网络/文件系统隔离

**Non-Goals:**

1. 不实现 Firecracker 沙箱（Docker 已足够）
2. 不实现工具市场 UI（属于 P4）

## Decisions

### D1: 使用 Docker 作为沙箱运行时

**选择**：Docker Python SDK

**理由**：
- 成熟稳定
- 资源限制完善（cgroups）
- 网络隔离简单
- 生态丰富

### D2: 沙箱池预热

**选择**：预创建 N 个沙箱容器，按需分配

**理由**：
- 避免每次创建容器的开销
- 支持快速分配
- 资源可控

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| Docker 逃逸漏洞 | 使用 seccomp + AppArmor |
| 资源泄漏 | 设置超时和回收机制 |
| 性能开销 | 沙箱池预热 |
