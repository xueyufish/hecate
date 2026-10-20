# Spec Delta

## MODIFIED Requirements

### Requirement: Compare view

The Compare view SHALL let the user select two runs and render the existing run-comparison API result: per-metric deltas, paired token/latency/cost deltas, and dataset/node drift indicators. The Run list SHALL distinguish loading, a genuine empty state, and query failure: failure SHALL consume the backend `detail.error.{code,message}` structure (and a plain string `detail` such as the comparison endpoint's 422 responses) and offer a retry, while a completed-filtered empty list SHALL render as a no-selectable-run empty state instead of an error. The comparison area SHALL distinguish not-submitted, in-flight, succeeded, and failed states: a failure SHALL show the backend `code/message` with a retry using the current selection instead of a generic fallback message, and Compare SHALL be disabled with an explicit prompt while the two selected runs are identical.

#### Scenario: Comparison renders deltas
- **WHEN** the user selects a baseline and a candidate run and submits
- **THEN** the view shows per-metric delta values, paired token/latency/cost deltas, and a drift badge when `dataset_drift` is present in the response

#### Scenario: Run list failure and retry

- **WHEN** the Run list query fails with the platform error envelope
- **THEN** the selection area shows the error `code/message` with a retry button instead of an empty list; retry re-issues the list query

#### Scenario: Structured and string error details are both consumed

- **WHEN** a comparison returns 404/403 with `detail.error.{code,message}`, or 422 with a plain string `detail` (such as runs referencing different datasets)
- **THEN** the comparison area shows the corresponding `code/message` or string text, not the generic "Comparison failed" fallback, with a retry for the current selection

#### Scenario: Identical runs disable comparison

- **WHEN** the user selects the same run as both baseline and candidate
- **THEN** the Compare button is disabled and an explicit same-run prompt is shown; no comparison request is issued

## ADDED Requirements

### Requirement: Compare 查询绑定有序 Run 对并丢弃迟到响应

比较结果 SHALL 绑定发起比较时的有序 Run 对(baseline, candidate)。baseline 或 candidate 改变 SHALL 立即清除旧比较结果与旧错误、使在途比较请求失效,并以未提交状态替换当前内容;旧请求的成功与错误响应 SHALL 一律丢弃,包括 A/B→A/C→A/B 回切后首个请求的迟到响应。新一次比较进行期间 SHALL 隐藏上一次比较结果(呈现比较中状态);失败后的重试 SHALL 使用当前选中的 Run 对重新发起。Run 列表查询 SHALL 支持手动重试;既有成功路径语义(逐指标 delta、回归判定徽标、配对 deltas 与 drift 展示)SHALL 保持不变。

#### Scenario: 选择变化清除旧结果

- **WHEN** Run 对 (A, B) 的比较结果已展示,用户改变 baseline 或 candidate
- **THEN** 旧结果与旧错误立即消失,比较区回到未提交状态,不残留 (A, B) 的内容

#### Scenario: 迟到成功与迟到错误均被丢弃

- **WHEN** 用户为 (A, B) 发起比较后改变选择,该在途请求晚于新状态返回
- **THEN** 无论成功或失败,该过期响应被丢弃,当前选择的内容与状态不受影响

#### Scenario: 回切后首个请求的迟到响应被丢弃

- **WHEN** 用户从 (A, B) 切到 (A, C) 再切回 (A, B),(A, B) 的首个比较请求在回切后才返回
- **THEN** 该过期响应被丢弃,视图展示回切后针对 (A, B) 重新发起的请求结果

#### Scenario: 重试使用当前 Run 对

- **WHEN** (A, C) 的比较失败后用户点击重试
- **THEN** 重试请求以 (A, C) 为 baseline/candidate 重新发起,不使用先前 Run 对

#### Scenario: 同值重选保持当前比较与重试

- **WHEN** 比较或失败后的重试仍在进行，用户重新选择 baseline 或 candidate 当前已选中的同一个 Run
- **THEN** 有序 Run 对没有变化，当前请求保持有效，不重发、不重置提交状态；响应返回后正常呈现成功或错误，不停留在比较中

#### Scenario: 同一 Run 对重复提交仅接受最新响应

- **WHEN** 同一有序 Run 对的前一比较尚未返回，用户再次点击 Compare，新请求先完成
- **THEN** 呈现新请求结果，前一请求的迟到成功和错误均不能覆盖它

#### Scenario: 交换 baseline 与 candidate 是不同 Run 对

- **WHEN** 用户已比较 (A,B)，随后将选择交换为 (B,A)
- **THEN** 旧结果清除，回到未提交状态；手动提交时按 (B,A) 原有序提交，不复用 (A,B) 的结果

#### Scenario: 列表字符串或客户端错误保留原因

- **WHEN** Run 列表失败返回字符串 detail，或 API client 提供顶层 error.code/message
- **THEN** 列表错误区展示对应原因和可用的 code，不降级成成功空列表或丢失原始 message
