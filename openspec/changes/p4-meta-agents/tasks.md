## 1. Garbage Collector Agent

- [x] 1.1 Create `services/meta_agents/garbage_collector.py` with GarbageCollectorAgent
- [x] 1.2 Implement scan_expired_sessions(retention_days) — find expired sessions
- [x] 1.3 Implement scan_orphaned_checkpoints() — find checkpoints without sessions
- [x] 1.4 Implement generate_cleanup_report() — resource counts and savings

## 2. Compliance Checker Agent

- [x] 2.1 Create `services/meta_agents/compliance_checker.py` with ComplianceCheckerAgent
- [x] 2.2 Implement check_code_style() — run ruff check
- [x] 2.3 Implement check_security_config() — verify security settings
- [x] 2.4 Implement generate_compliance_report() — violations and recommendations

## 3. Drift Detector Agent

- [x] 3.1 Create `services/meta_agents/drift_detector.py` with DriftDetectorAgent
- [x] 3.2 Implement detect_config_drift() — compare actual vs expected
- [x] 3.3 Implement generate_drift_report() — drift details and impact

## 4. Scheduling

- [x] 4.1 Add cron scheduling for meta agents
- [x] 4.2 Add configuration for scan frequency

## 5. Testing

- [x] 5.1 Unit tests for GarbageCollectorAgent — scan, report
- [x] 5.2 Unit tests for ComplianceCheckerAgent — check, report
- [x] 5.3 Unit tests for DriftDetectorAgent — detect, report
