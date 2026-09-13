# Delta: workflow-evaluation

## MODIFIED Requirements

### Requirement: Dataset snapshot frozen at run start

The system SHALL, at the start of every offline run, snapshot the dataset's item set into a run-attached `dataset_snapshot` field containing the canonical JSON of every item plus a content `hash`. The run SHALL compare the `hash` against the dataset's current hash at run completion; the resulting run record SHALL expose the snapshot hash, the dataset's current hash, and (when they differ) the list of `item_ids` that changed since snapshot. The snapshot SHALL be the source of truth for "which items the run executed against" — when comparing two runs for regression, the diff endpoint SHALL use the snapshot rather than the live dataset.

The snapshot SHALL additionally carry each item's known-bad marker (the marker fields travel on the snapshot item only when the item is marked), and run aggregation SHALL read exemption state from the snapshot, never from the live dataset at aggregation time. The content `hash` SHALL be computed over the evaluation-content fields only (`query`, `expected_answer`, `context`, `tags`, `metadata`) — marking or clearing an exemption SHALL NOT change the hash and SHALL NOT surface as `dataset_drift`.

#### Scenario: Snapshot captured at run start

- **WHEN** an offline run starts against a dataset
- **THEN** the run record SHALL carry a `dataset_snapshot` with the items' canonical JSON and a hash, persisted before any item executes; items marked known-bad carry their marker on the snapshot item, unmarked items serialize without any marker keys

#### Scenario: Snapshot hash compared against current dataset hash

- **WHEN** an item is marked or unmarked as known-bad after the snapshot is taken but before run completion
- **THEN** the post-run hash comparison SHALL report no `dataset_drift` (exemption changes do not affect the content hash)

#### Scenario: Diff uses snapshot, not live dataset

- **WHEN** `POST /api/evaluation/runs/compare` is called with two runs whose snapshots differ
- **THEN** the response SHALL align items by snapshot position, not by current dataset ordering, and SHALL report `dataset_drift` describing the hash mismatch

#### Scenario: Aggregation reads snapshot-time exemption state

- **WHEN** an item is marked known-bad after a run has completed
- **THEN** the completed run's summary SHALL remain based on the snapshot-time state (unchanged), and only runs started after the marking SHALL exclude the item

## ADDED Requirements

### Requirement: Known-bad exemption in run aggregation

The system SHALL exclude known-bad items from run quality aggregation while still executing them and recording their scores. Known-bad items SHALL NOT contribute to the numerator or the denominator of `pass_rate`, SHALL NOT contribute to `consistency_rate` (when `repetitions > 1`), SHALL NOT contribute to `metric_averages`, and SHALL NOT contribute to the per-metric averages used for regression determination (`is_regression`). The run summary SHALL include an `exempted_items` count of snapshot items excluded by this requirement. When a known-bad item's repetitions all meet their metric threshold, the summary SHALL list the item's id in `known_bad_passed_item_ids` as an explicit "dataset healed" signal; the system SHALL NOT automatically clear the exemption. The three-state CLI exit code semantics SHALL operate on the excluded-aggregation `pass_rate` without change.

#### Scenario: Exempted item removed from pass_rate denominator

- **WHEN** a run executes a dataset of 10 items of which 2 are marked known-bad in the snapshot, and 6 of the 8 active items pass
- **THEN** `pass_rate` SHALL be `0.75` (6 of 8), the summary SHALL report `exempted_items: 2`, and all 10 items SHALL still have scores recorded

#### Scenario: Exempted item passing is surfaced, not auto-cleared

- **WHEN** a known-bad item's repetitions all meet their threshold in a run
- **THEN** the item's id SHALL appear in `summary.known_bad_passed_item_ids`, the item SHALL remain known-bad after the run, and `pass_rate` SHALL NOT count it

#### Scenario: Exempted items excluded from regression averages

- **WHEN** `POST /api/evaluation/runs/compare` compares a baseline and a candidate run that both contain known-bad items
- **THEN** per-metric `baseline_avg`, `candidate_avg`, and `is_regression` SHALL be computed over active items only, for both runs

#### Scenario: Single-repetition summary with exemptions

- **WHEN** a run with `repetitions = 1` executes a dataset containing known-bad items
- **THEN** the summary SHALL include `pass_rate` and `exempted_items`, and SHALL NOT include `consistency_rate`
