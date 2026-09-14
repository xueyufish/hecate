## ADDED Requirements

### Requirement: 运行时按执行上下文解析工作流版本

工作流执行 SHALL 按执行上下文解析所用版本：studio 编辑器内的试跑 SHALL 使用最新草稿版本；其余执行路径 SHALL 优先使用 `published_version`，该工作流从未发布过时 SHALL 回落使用最新版本（保持存量行为）。版本一经解析，本次执行的整条图 SHALL 使用同一版本的定义。

#### Scenario: 已发布工作流执行已发布版

- **WHEN** 工作流 W 的 published_version=7、最新版本=9（草稿），非 studio 试跑路径触发 W 的执行
- **THEN** 执行 SHALL 加载版本 7 的图定义，SHALL NOT 使用版本 9

#### Scenario: 从未发布的工作流回落最新版

- **WHEN** 工作流 W 从未发布（published_version 为空），触发执行
- **THEN** 执行 SHALL 使用最新版本，行为与存量一致

#### Scenario: studio 试跑走草稿

- **WHEN** 在 studio 编辑器内对 W（published_version=7、最新版本=9）发起试跑
- **THEN** 试跑 SHALL 使用版本 9（最新草稿）

#### Scenario: 版本解析单次执行内一致

- **WHEN** 一次执行解析到版本 7 后，另一次发布将 published_version 改为 8
- **THEN** 进行中的那次执行 SHALL 全程使用版本 7 的定义
