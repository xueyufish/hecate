"use client";

import { useRouter } from "next/navigation";
import { api } from "@/lib/api-client";
import { AgentConfigurator, AgentFormData } from "@/components/agent/agent-configurator";

export default function NewAgentPage() {
  const router = useRouter();

  const handleSubmit = async (data: AgentFormData) => {
    const agent = await api.post<{ id: string }>("/api/agents", {
      name: data.name,
      mode: data.mode,
      model_config: { model: data.model },
      persona: data.persona || undefined,
      tools: data.tools,
      skills: data.skills,
      knowledge_base_ids: data.knowledge_base_ids,
      risk_level: data.risk_level,
      opening_remarks: data.opening_remarks || undefined,
      enable_suggestions: data.enable_suggestions,
    });
    router.push(`/agents/${agent.id}`);
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold">Create Agent</h1>
      <AgentConfigurator onSubmit={handleSubmit} submitLabel="Create Agent" />
    </div>
  );
}
