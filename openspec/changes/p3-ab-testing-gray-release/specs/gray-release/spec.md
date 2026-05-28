## ADDED Requirements

### Requirement: Weighted routing for gray release
The system SHALL route traffic based on configurable weights for gradual rollout.

#### Scenario: 90/10 gray release
- **WHEN** new model version is deployed with 10% traffic
- **THEN** the system routes 90% to old version and 10% to new version

#### Scenario: Progressive rollout
- **WHEN** gray release is progressing (10% → 30% → 50% → 100%)
- **THEN** the system updates traffic weights accordingly
