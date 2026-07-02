## MODIFIED Requirements

### Requirement: KnowledgeBaseModel with embedding and search config
The `KnowledgeBaseModel` SHALL use `collection_name` as the column storing the vector store collection identifier, replacing the previous `qdrant_collection` column. An Alembic migration SHALL rename the existing column.

#### Scenario: Default embedding model
- **WHEN** a knowledge base is created
- **THEN** `embedding_model` SHALL default to "BAAI/bge-m3"

#### Scenario: Search mode options
- **WHEN** search_mode is set
- **THEN** it SHALL accept "hybrid" (default), "dense", or "sparse"

#### Scenario: Collection name field
- **WHEN** a knowledge base is created and a vector store collection is initialized
- **THEN** `collection_name` SHALL store the backend-agnostic collection identifier

#### Scenario: CreateSchema uses collection_name
- **WHEN** `KnowledgeBaseCreateSchema` is constructed
- **THEN** the collection field SHALL be named `collection_name` (not `qdrant_collection`)

#### Scenario: ReadSchema serializes collection_name
- **WHEN** `KnowledgeBaseReadSchema` is serialized
- **THEN** the collection field SHALL appear as `collection_name` in the JSON output
