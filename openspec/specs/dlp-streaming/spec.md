# dlp-streaming Specification

## Purpose
TBD - created by archiving change outbound-dlp-engine. Update Purpose after archive.
## Requirements
### Requirement: StreamingDLPWrapper
The system SHALL define `StreamingDLPWrapper` that buffers streaming output chunks, applies DLPScanner.scan() at configurable buffer threshold, and handles BLOCK/MASK/AUDIT actions during streaming.

#### Scenario: Buffer threshold triggers scan
- **WHEN** buffer length exceeds `DLP_STREAM_BUFFER_SIZE` (default 300 chars)
- **THEN** the wrapper SHALL call DLPScanner.scan() on the buffer

#### Scenario: Overlap retention
- **WHEN** buffer is flushed
- **THEN** the last `DLP_STREAM_OVERLAP` chars (default 10) SHALL be retained in the buffer for the next scan

#### Scenario: BLOCK stops stream
- **WHEN** scan result action is BLOCK
- **THEN** `process_chunk()` SHALL return `None` and the caller SHALL stop the stream

#### Scenario: MASK marks for post-stream correction
- **WHEN** scan result action is MASK
- **THEN** the wrapper SHALL mark finding and continue streaming (post-stream correction in v1)

#### Scenario: AUDIT logs and continues
- **WHEN** scan result action is AUDIT
- **THEN** the wrapper SHALL record audit_data and continue streaming

#### Scenario: Final scan on stream end
- **WHEN** `finalize()` is called
- **THEN** the wrapper SHALL run DLPScanner.scan() on the full accumulated text

#### Scenario: Correction message on MASK findings
- **WHEN** finalize() detects MASK findings from incremental or final scan
- **THEN** the wrapper SHALL return a correction flag indicating the caller should append a correction message

### Requirement: Streaming DLP configuration
The system SHALL provide settings `DLP_STREAM_ENABLED` (bool, default True), `DLP_STREAM_BUFFER_SIZE` (int, default 300), `DLP_STREAM_OVERLAP` (int, default 10), `DLP_STREAM_FINAL_SCAN` (bool, default True), `DLP_STREAM_MASK_CORRECTION` (bool, default True).

#### Scenario: Disabling streaming DLP
- **WHEN** `DLP_STREAM_ENABLED=False`
- **THEN** StreamingDLPWrapper SHALL pass through chunks without scanning

#### Scenario: Custom buffer size
- **WHEN** `DLP_STREAM_BUFFER_SIZE=500`
- **THEN** scans SHALL occur every 500 chars instead of 300

