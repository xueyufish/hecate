export interface Agent {
  id: string;
  name: string;
  persona: string | null;
  mode: string;
  model_config: { model?: string };
  model_available?: boolean | null;
  tools: string[];
  skills: string[];
  knowledge_base_ids: string[];
  risk_level: string;
  opening_remarks: string | null;
  enable_suggestions: boolean;
  created_at: string;
  updated_at: string;
}

export interface Tool {
  id: string;
  name: string;
  description: string;
  type: string;
}

export interface Skill {
  id: string;
  name: string;
  description: string;
}

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  document_count?: number;
}

// Agent versioning & channel publishing (1.3.20).

export interface AgentVersionPinRef {
  resource_type: string;
  resource_id: string;
  version: number | null;
}

export interface AgentVersionManifestEntry {
  resource_type: string;
  resource_id: string;
  version: number | null;
  content_hash: string | null;
}

export interface AgentVersion {
  id: string;
  agent_id: string;
  version: number;
  name: string;
  change_summary: string;
  content_hash: string;
  is_published: boolean | null;
  created_by: string | null;
  workspace_id: string;
  created_at: string;
  schema_version?: number;
  config_snapshot?: Record<string, unknown>;
  pinned_refs?: AgentVersionPinRef[];
  ref_manifest?: AgentVersionManifestEntry[];
}

export interface AgentVersionStatus {
  agent_id: string;
  latest_version: number | null;
  published_version: number | null;
  has_uncommitted_changes: boolean;
}

export interface AgentVersionDrift {
  agent_id: string;
  version: number;
  drifted: {
    resource_type: string;
    resource_id: string;
    committed_hash: string | null;
    live_hash: string | null;
    missing: boolean;
  }[];
}

export interface VersionDiff {
  v1: number;
  v2: number;
  identical: boolean;
  summary: Record<string, number | string>;
  details: {
    reference_changes?: {
      change: string;
      resource_type: string;
      resource_id: string;
      v1_hash?: string | null;
      v2_hash?: string | null;
    }[];
    config_changes?: Record<string, unknown>;
  };
}

export interface ChannelEntry {
  id: string;
  workspace_id: string;
  name: string;
  type: "api" | "im" | "embed" | "webhook";
  agent_id: string;
  bind_mode: "published" | "pinned";
  pinned_version: number | null;
  config: Record<string, unknown>;
  status: "active" | "unwired" | "disabled";
  created_at: string;
}
