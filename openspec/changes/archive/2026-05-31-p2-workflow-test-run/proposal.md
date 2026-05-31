## Why

The workflow editor has a basic test run button that executes the entire workflow and shows per-node status (completed/failed). However, it lacks **input customization** (hardcoded to "test"), **output preview** (no way to see what each node produced), and **execution logs** (no debugging information). The feature catalog describes this as "单步调试、输入输出预览、执行日志" — step debugging, input/output preview, and execution logs.

## What Changes

- Add **custom input form** — user can specify input data (messages, variables) before running
- Add **node output panel** — click a node to see its input/output data after test run
- Add **execution logs panel** — show detailed logs for each node (timing, errors, intermediate data)
- Add **node status highlighting** — visually indicate node status on canvas (pending/running/completed/failed)
- Add **run history sidebar** — show previous test runs with ability to compare results

## Capabilities

### New Capabilities
- `workflow-test-run`: Enhanced workflow testing with custom input, node output preview, execution logs, and run history

### Modified Capabilities
- (none — existing spec is not formalized)

## Impact

- **Backend**: Add endpoint to fetch run details with node outputs/logs
- **Backend**: Store node outputs in WorkflowRunModel.node_results
- **Frontend**: Workflow editor — add input form, output panel, logs panel, run history
- **Tests**: API tests for run details endpoint
