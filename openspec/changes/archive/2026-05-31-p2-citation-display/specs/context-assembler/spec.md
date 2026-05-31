## MODIFIED Requirements

### Requirement: Knowledge injection with citation position mapping
The `ContextAssembler` SHALL accept a `knowledge` parameter (list of chunk dicts) and inject formatted numbered reference blocks into the LLM context. Each chunk SHALL be assigned a sequential position number. The assembler SHALL return a `citation_map` in `AssembledContext.metadata` mapping position numbers to citation metadata.

#### Scenario: Knowledge chunks injected as numbered references
- **WHEN** `knowledge` parameter is provided with 3 chunks
- **THEN** the assembler SHALL format them as `[1] "text..." (Source: filename)\n[2] ...` and inject them into the system message, and return `metadata.citation_map` with `{1: {chunk_id, kb_id, ...}, 2: {...}, 3: {...}}`

#### Scenario: Knowledge parameter is None or empty
- **WHEN** `knowledge` parameter is None or empty list
- **THEN** the assembler SHALL proceed without injection (no change to messages)

#### Scenario: Chunk content exceeds 500 characters
- **WHEN** a knowledge chunk's content exceeds 500 characters
- **THEN** the assembler SHALL truncate the content to 500 characters with "..." suffix before injection
