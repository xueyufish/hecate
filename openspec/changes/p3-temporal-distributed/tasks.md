## 1. Temporal Integration

- [x] 1.1 Add `temporalio` dependency to pyproject.toml
- [x] 1.2 Create `engine/temporal/worker_pool.py` with TemporalWorkerPool class
- [x] 1.3 Implement Activity execution — wrap node execution as Temporal Activity
- [x] 1.4 Implement heartbeat support — send heartbeat during long-running Activities
- [x] 1.5 Create `engine/temporal/workflow.py` with DistributedPregelWorkflow
- [x] 1.6 Implement Continue-As-New — reset workflow history after N supersteps
- [x] 1.7 Implement checkpoint persistence via Temporal Activities

## 2. Conflict Resolution

- [x] 2.1 Create `engine/temporal/conflict.py` with ConflictResolver class
- [x] 2.2 Implement optimistic locking — version check before channel update
- [x] 2.3 Implement merge strategies — list append, map merge, last-write-wins
- [ ] 2.4 Implement human approval — Temporal Signal for critical conflicts
- [ ] 2.5 Integrate with PregelRuntime — apply conflict resolution on channel updates

## 3. Configuration

- [x] 3.1 Add Temporal configuration to Settings — server URL, task queue, timeouts
- [ ] 3.2 Create Temporal Worker startup script
- [ ] 3.3 Add Docker Compose configuration for Temporal server

## 4. Testing

- [x] 4.1 Unit tests for TemporalWorkerPool — dispatch, timeout, heartbeat
- [ ] 4.2 Unit tests for DistributedPregelWorkflow — execution, Continue-As-New
- [x] 4.3 Unit tests for ConflictResolver — optimistic locking, merge, approval
- [ ] 4.4 Integration test with Temporal test environment
