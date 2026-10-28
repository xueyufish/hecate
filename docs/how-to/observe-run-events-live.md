# Observe a run's events live

The Task detail page (`/ops-center/tasks/{task_id}`) can follow one selected Run's event stream in real time, on top of the cursor-paginated event history. Observation is read-only: starting, pausing or leaving it only opens or closes an HTTP connection — it never issues a control command and never affects the executing run.

## How it works

- The subscription uses the existing `GET /api/runs/{run_id}/events/stream` endpoint with the same workspace, visibility and VIEWER checks as every other read.
- Observation starts after the page currently in view (the resume position the server returned for that window) and appends only what the stream delivers. Older events stay reachable through the paginated history above.
- On a dropped connection the panel reconnects from the last **accepted** envelope's `source_sequence` — the server's own cursor rule. Replayed positions cannot move the cursor backwards or replenish the reconnect budget. Gaps render only as server-provided gap rows; the panel never invents missing events from sequence jumps.
- A frame that is malformed or does not match the envelope shape stops the observation with an explicit error — data is never silently skipped, and the resume cursor never advances over a frame the parser could not fully accept (including a connection that dies mid-frame).

## Connection states

| State | Meaning |
| --- | --- |
| 连接中 / 重连中 | Opening the stream, or reconnecting with bounded backoff after a network drop |
| 已连接 | The HTTP stream is open, including while idle; new events appear as they are recorded |
| 观察结束 | A terminal event arrived (`hecate.platform.run_terminal/0`, or the entry-side terminal envelope), or an authorized Run query confirmed terminal state after EOF. The panel distinguishes these cases and refreshes Task detail; neither is a claim that execution succeeded |
| 观察已终止 | The server explicitly ended the stream (`event: error` frame, e.g. access revoked mid-stream — the status code is shown), the connection was refused before it opened (HTTP 403/404), or a frame could not be interpreted. None of these retry |
| 观察已停止 | The reconnect budget ran out without observing a terminal event. The panel offers to refresh the history; the authoritative state is the Task detail, never the stream's silence |

An empty page, `has_more=false` and a clean EOF are never treated as execution success.
EOF triggers an authorized Run query before retrying. If the Run is terminal, observation ends without repeated reconnections. Switching Runs or leaving the page cancels this query along with the stream; returning to an earlier Run starts idle.

## Buffer and history

The live view keeps a bounded buffer of the newest events; when it overflows the panel says so explicitly and older events remain available through the paginated history query. Authentication reuses the web client's Bearer token (header only, never in the URL) with the same single-flight refresh as every other API call; a pre-connection 401 refreshes once, while an in-stream revocation terminates the observation as shown above.

## What this surface does not do

- It does not display raw payloads, hidden reasoning or full context — the same observation-field whitelist as the paginated view applies.
- It does not send control commands: pausing observation, switching Runs or leaving the page only cancels the request, the reader and any pending reconnect; late frames from a stopped stream are discarded.
