## ADDED Requirements

### Requirement: Streaming Response Display
The model debug page SHALL support streaming responses via `/v1/chat/completions` with `stream: true`. The response content SHALL appear progressively as chunks arrive, with a typing indicator while streaming is active.

#### Scenario: Streaming response
- **WHEN** the user clicks "Run Test" with streaming enabled
- **THEN** the response area SHALL show content appearing progressively, with a typing indicator until complete

#### Scenario: Non-streaming fallback
- **WHEN** streaming is disabled or fails
- **THEN** the system SHALL fall back to non-streaming mode and display the complete response

### Requirement: System Prompt Support
The model debug page SHALL provide a system prompt input field. When provided, the system message SHALL be included in the `/v1/chat/completions` request as the first message.

#### Scenario: Test with system prompt
- **WHEN** the user enters a system prompt and runs the test
- **THEN** the request SHALL include `{"role": "system", "content": "..."}` as the first message

#### Scenario: Empty system prompt
- **WHEN** the system prompt field is empty
- **THEN** the request SHALL only include the user message

### Requirement: Response Time Measurement
The model debug page SHALL measure and display:
- **Time to First Token (TTFT)** — milliseconds from request start to first content chunk
- **Total Time** — milliseconds from request start to response completion

#### Scenario: Latency display
- **WHEN** a test completes
- **THEN** the system SHALL display TTFT and total time in milliseconds

### Requirement: Token Usage Visualization
The model debug page SHALL display token usage as a visual progress bar showing the ratio of prompt tokens to completion tokens, with numeric labels.

#### Scenario: Token usage bar
- **WHEN** a test completes with usage data
- **THEN** the system SHALL show a progress bar with prompt tokens (blue) and completion tokens (green), with numeric labels

### Requirement: Test History
The model debug page SHALL save the last 10 tests in localStorage. Each history entry SHALL include: model, prompt (truncated to 100 chars), system prompt (truncated to 100 chars), temperature, max_tokens, response (truncated to 500 chars), timestamp, and latency.

#### Scenario: View test history
- **WHEN** the user clicks "History" button
- **THEN** the system SHALL display a list of the last 10 tests with model, prompt preview, and timestamp

#### Scenario: Load history entry
- **WHEN** the user clicks a history entry
- **THEN** the system SHALL populate the form with the saved parameters (model, prompts, temperature, max_tokens)

#### Scenario: Clear history
- **WHEN** the user clicks "Clear History"
- **THEN** the system SHALL remove all history entries from localStorage

### Requirement: Error Display Improvements
The model debug page SHALL display error messages with:
- Error code and message from the API
- Suggested action (e.g., "Check API key", "Model not available", "Rate limited")

#### Scenario: Provider error
- **WHEN** the API returns a provider-specific error
- **THEN** the system SHALL display the error message with a suggestion for resolution

#### Scenario: Network error
- **WHEN** a network error occurs
- **THEN** the system SHALL display "Network error — check your connection" with a retry button
