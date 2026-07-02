## MODIFIED Requirements

### Requirement: Settings loaded from environment variables and .env file
The `Settings` class (pydantic-settings) SHALL include `VECTOR_STORE_TYPE` (default `"qdrant"`) and per-backend connection settings: `QDRANT_URL` (default `"http://localhost:6333"`), `QDRANT_API_KEY` (default `""`), and `CHROMA_PERSIST_DIR` (default `"./data/chroma"`). The existing `QDRANT_URL` field SHALL be retained for backward compatibility when `VECTOR_STORE_TYPE=qdrant`.

#### Scenario: Default values
- **WHEN** no environment variables are set
- **THEN** `Settings` SHALL use defaults: `VECTOR_STORE_TYPE="qdrant"`, `QDRANT_URL="http://localhost:6333"`, `CHROMA_PERSIST_DIR="./data/chroma"`

#### Scenario: Qdrant configuration
- **WHEN** `VECTOR_STORE_TYPE=qdrant` and `QDRANT_URL=http://custom:6333`
- **THEN** the QdrantVectorStore SHALL connect to the specified URL

#### Scenario: Chroma configuration
- **WHEN** `VECTOR_STORE_TYPE=chroma` and `CHROMA_PERSIST_DIR=/data/vecs`
- **THEN** the ChromaVectorStore SHALL use `/data/vecs` as the persistence directory

#### Scenario: Unsupported vector store type
- **WHEN** `VECTOR_STORE_TYPE` is set to an unrecognized value
- **THEN** `get_vector_store()` SHALL raise `ValueError` at runtime
