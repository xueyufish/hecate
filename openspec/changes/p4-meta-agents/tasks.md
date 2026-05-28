## 1. Garbage Collector Agent

- [ ] 1.1 Create `services/meta_agents/garbage_collector.py` with GarbageCollectorAgent
- [ ] 1.2 Implement scan_expired_sessions(retention_days) — find expired sessions
- [ ] 1.3 Implement scan_orphaned_checkpoints() — find checkpoints without sessions
- [ ] 1.4 Implement generate_cleanup_report() — resource counts and savings

## 2. Compliance Checker Agent

- [ ] 2.1 Create `services/meta_agents/compliance_checker.py` with ComplianceCheckerAgent
- [ ] 2.2 Implement check_code_style() — run ruff check
- [ ] 2.3 Implement check_security_config() — verify security settings
- [ ] 2.4 Implement generate_compliance_report() — violations and recommendations

## 3. Drift Detector Agent

- [ ] 3.1 Create `services/meta_agents/drift_detector.py` with DriftDetectorAgent
- [ ] 3.2 Implement detect_config_drift() — compare actual vs expected
- [ ] 3.3 Implement generate_drift_report() — drift details and impact

## 4. Scheduling

- [ ] 4.1 Add cron scheduling for meta agents
- [ ] 4.2 Add configuration for scan frequency

## 5. Testing

- [ ] 5.1 Unit tests for GarbageCollectorAgent — scan, report
- [ ] 5.2 Unit tests for ComplianceCheckerAgent — check, report
- [ ] 5.3 Unit tests for DriftDetectorAgent — detect, report
