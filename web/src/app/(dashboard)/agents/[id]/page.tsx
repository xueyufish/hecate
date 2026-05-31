"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api-client";
import { Agent } from "@/lib/api-types";
import { AgentConfigurator, AgentFormData } from "@/components/agent/agent-configurator";
import { AlertTriangle } from "lucide-react";

export default function AgentDetailPage() {
  const params = useParams();
  const router = useRouter();
  const agentId = params.id as string;
  const [agent, setAgent] = useState<Agent | null>(null);
  const [loading, setLoading] = useState(true);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    api
      .get<Agent>(`/api/agents/${agentId}`)
      .then(setAgent)
      .catch(() => setAgent(null))
      .finally(() => setLoading(false));
  }, [agentId]);

  const handleSubmit = async (data: AgentFormData) => {
    await api.put(`/api/agents/${agentId}`, {
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
    setSuccess(true);
    setTimeout(() => setSuccess(false), 3000);
  };

  if (loading) {
    return <div className="text-muted-foreground">Loading...</div>;
  }

  if (!agent) {
    return <div className="text-muted-foreground">Agent not found</div>;
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">{agent.name}</h1>
        <button
          onClick={() => router.push(`/chat/${agentId}`)}
          className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground hover:bg-primary/90"
        >
          Start Chat
        </button>
      </div>

      {agent.model_available === false && (
        <div className="flex items-center gap-2 rounded-md border border-yellow-300 bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>
            Model &quot;{agent.model_config?.model}&quot; is unavailable. Check provider settings.
          </span>
        </div>
      )}

      {success && (
        <div className="rounded-md border border-green-300 bg-green-50 px-4 py-3 text-sm text-green-800">
          Agent updated successfully!
        </div>
      )}

      <AgentConfigurator
        initialData={{
          name: agent.name,
          persona: agent.persona || "",
          model: agent.model_config?.model || "",
          mode: agent.mode,
          tools: agent.tools,
          skills: agent.skills,
          knowledge_base_ids: agent.knowledge_base_ids,
          risk_level: agent.risk_level,
          opening_remarks: agent.opening_remarks || "",
          enable_suggestions: agent.enable_suggestions,
        }}
        onSubmit={handleSubmit}
        submitLabel="Save Changes"
        agentId={agentId}
      />
    </div>
  );
}
