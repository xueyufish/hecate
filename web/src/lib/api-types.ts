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
