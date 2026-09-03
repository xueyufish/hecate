# Documentation writing style

## No specific numbers or dates as descriptive markers

`README.md` and `docs/**` (temporarily exempt: `docs/design/adr/`,
`docs/features/`) must not contain specific counts or dates as
descriptive/marketing markers — e.g. "32 ADRs", "1713 tests",
"shipped 2026-08-22". Use vague qualifiers (`many`, `several`, `recent`) or
drop the number.

**Rationale**: counts and dates go stale; a number that is wrong is worse
than no number.

**Allowed exceptions**:

- Functional version requirements (`Python 3.12+`, `SQLAlchemy 2.0`)
- HTTP status codes / port numbers / file sizes in commands
- Dates inside JSON examples and `openspec/` archive folder names
- Project-internal IDs (`P1`–`P5`, feature codes like `13.5`)
- Quantitative hardware minimums in install instructions
- Factual observations in `docs/research/`
