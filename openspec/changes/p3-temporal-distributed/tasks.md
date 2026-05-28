## 1. Temporal Integration

- [ ] 1.1 Add `temporalio` dependency to pyproject.toml
- [ ] 1.2 Create `engine/temporal/worker_pool.py` with TemporalWorkerPool class
- [ ] 1.3 Implement Activity execution — wrap node execution as Temporal Activity
- [ ] 1.4 Implement heartbeat support — send heartbeat during long-running Activities
- [ ] 1.5 Create `engine/temporal/workflow.py` with DistributedPregelWorkflow
- [ ] 1.6 Implement Continue-As-New — reset workflow history after N supersteps
- [ ] 1.7 Implement checkpoint persistence via Temporal Activities

## 2. Conflict Resolution

- [ ] 2.1 Create `engine/temporal/conflict.py` with ConflictResolver class
- [ ] 2.2 Implement optimistic locking — version check before channel update
- [ ] 2.3 Implement merge strategies — list append, map merge, last-write-wins
- [ ] 2.4 Implement human approval — Temporal Signal for critical conflicts
- [ ] 2.5 Integrate with PregelRuntime — apply conflict resolution on channel updates

## 3. Configuration

- [ ] 3.1 Add Temporal configuration to Settings — server URL, task queue, timeouts
- [ ] 3.2 Create Temporal Worker startup script
- [ ] 3.3 Add Docker Compose configuration for Temporal server

## 4. Testing

- [ ] 4.1 Unit tests for TemporalWorkerPool — dispatch, timeout, heartbeat
- [ ] 4.2 Unit tests for DistributedPregelWorkflow — execution, Continue-As-New
- [ ] 4.3 Unit tests for ConflictResolver — optimistic locking, merge, approval
- [ ] 4.4 Integration test with Temporal test environment
