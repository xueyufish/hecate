## ADDED Requirements

### Requirement: Conversation embedding generation
The system SHALL generate embeddings for completed conversations using the existing RAG embedding service. The embedding SHALL be computed from the conversation's message content (concatenated user and assistant messages). Embeddings SHALL be stored in Qdrant in a dedicated `conversation_embeddings` collection.

#### Scenario: Generate embedding for a completed conversation
- **WHEN** a conversation completes and quality scoring is triggered
- **THEN** the system generates an embedding from the conversation's message content and stores it in Qdrant with conversation_id as the point ID

#### Scenario: Embedding generation uses existing RAG service
- **WHEN** the system needs to generate a conversation embedding
- **THEN** it uses the same embedding model and provider configured for the RAG pipeline

### Requirement: Initial topic clustering via HDBSCAN
The system SHALL perform initial topic clustering on conversation embeddings using HDBSCAN. The system SHALL automatically determine the number of clusters (no predefined k). Clusters SHALL be stored in ConversationClusterModel with label, centroid embedding, and quality metrics.

#### Scenario: Initial clustering discovers topic clusters
- **WHEN** 100 unclustered conversations accumulate
- **THEN** the system runs HDBSCAN on their embeddings and creates ConversationClusterModel records for each discovered cluster

#### Scenario: HDBSCAN auto-selects cluster count
- **WHEN** HDBSCAN runs on 100 embeddings
- **THEN** it automatically determines the optimal number of clusters based on density (e.g., 5 clusters)

#### Scenario: Cluster centroid computed
- **WHEN** a cluster is created with 20 conversation embeddings
- **THEN** the system computes the centroid (mean embedding) and stores it in the cluster record

### Requirement: Incremental conversation-to-cluster matching
The system SHALL match new conversations to existing clusters using embedding cosine similarity. The system SHALL use a two-stage approach: (1) cosine similarity filtering to find top-5 candidate clusters, (2) LLM semantic confirmation for ambiguous matches. Unmatched conversations SHALL accumulate in an "unclassified pool" until enough similar conversations form a new cluster.

#### Scenario: New conversation matches existing cluster
- **WHEN** a new conversation embedding has cosine similarity > 0.8 to an existing cluster centroid
- **THEN** the system assigns the conversation to that cluster without LLM confirmation

#### Scenario: Ambiguous match requires LLM confirmation
- **WHEN** a new conversation embedding has cosine similarity between 0.5 and 0.8 to multiple clusters
- **THEN** the system uses LLM to confirm which cluster is the best match

#### Scenario: No match found
- **WHEN** a new conversation embedding has cosine similarity < 0.5 to all clusters
- **THEN** the system adds the conversation to the "unclassified pool"

#### Scenario: New cluster creation from unclassified pool
- **WHEN** the unclassified pool accumulates 10+ similar conversations
- **THEN** the system uses LLM to determine if they form a meaningful new cluster, and creates one if so

### Requirement: LLM-generated topic labels
The system SHALL generate human-readable topic labels for each cluster using LLM. The LLM SHALL analyze a sample of conversations in the cluster and generate a concise label (e.g., "billing", "technical_support", "feature_request"). Labels SHALL be stored in ConversationClusterModel.label.

#### Scenario: Generate label for new cluster
- **WHEN** a new cluster is created with 15 conversations
- **THEN** the system samples 5 conversations, sends them to LLM, and receives a label like "technical_support"

#### Scenario: Label update when cluster content changes
- **WHEN** a cluster's content changes significantly (e.g., 50% new conversations added)
- **THEN** the system re-generates the label using the updated cluster contents

### Requirement: Cluster quality monitoring
The system SHALL compute quality metrics for each cluster: DBI (Davies-Bouldin Index), Silhouette Score, and Cohesion Score (intra-cluster similarity). The system SHALL monitor these metrics and flag clusters that degrade below thresholds.

#### Scenario: Compute cluster quality metrics
- **WHEN** a cluster has 20 conversation embeddings
- **THEN** the system computes DBI, Silhouette, and Cohesion scores and stores them in the cluster record

#### Scenario: Detect cluster degradation
- **WHEN** a cluster's Silhouette score drops below 0.5
- **THEN** the system flags the cluster for refinement

### Requirement: Cluster refinement via splitting and merging
The system SHALL refine degraded clusters by splitting overly broad clusters and merging similar clusters. Splitting SHALL use LLM to identify sub-topics within a cluster. Merging SHALL combine clusters with centroid similarity > 0.9.

#### Scenario: Split degraded cluster
- **WHEN** a cluster has Silhouette score < 0.5 and contains 50+ conversations
- **THEN** the system uses LLM to identify 2-3 sub-topics and splits the cluster accordingly

#### Scenario: Merge similar clusters
- **WHEN** two clusters have centroid similarity > 0.9
- **THEN** the system merges them into a single cluster and re-generates the label

### Requirement: Topic clustering configuration
The system SHALL support configurable settings: `CONVERSATION_CLUSTERING_ENABLED` (default True), `CONVERSATION_CLUSTERING_MIN_CLUSTER_SIZE` (default 10), `CONVERSATION_CLUSTERING_SIMILARITY_THRESHOLD` (default 0.5), `CONVERSATION_CLUSTERING_CONFIRMATION_THRESHOLD` (default 0.8).

#### Scenario: Clustering disabled via configuration
- **WHEN** `CONVERSATION_CLUSTERING_ENABLED` is False
- **THEN** the system skips embedding generation and cluster matching

#### Scenario: Custom minimum cluster size
- **WHEN** `CONVERSATION_CLUSTERING_MIN_CLUSTER_SIZE` is set to 20
- **THEN** new clusters are only created when 20+ similar unclassified conversations accumulate

### Requirement: Topic distribution API
The system SHALL expose `GET /api/ops-center/conversations/topics` returning topic distribution: list of topics with conversation count and average quality score. Supports `start_date`, `end_date` query parameters.

#### Scenario: Get topic distribution
- **WHEN** a client requests `GET /api/ops-center/conversations/topics?start_date=...&end_date=...`
- **THEN** the system returns `[{topic: "technical_support", count: 45, avg_quality: 0.72}, {topic: "billing", count: 30, avg_quality: 0.85}]`

#### Scenario: Topic distribution includes unclassified
- **WHEN** some conversations are not yet clustered
- **THEN** the system includes an "unclassified" topic entry with count of unclustered conversations
