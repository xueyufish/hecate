"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { AgentConfigurator, AgentFormData } from "@/components/agent/agent-configurator";
import { TemplatePicker } from "@/components/agent/template-picker";
import { LayoutTemplate } from "lucide-react";

export default function NewAgentPage() {
  const router = useRouter();
  const [showTemplatePicker, setShowTemplatePicker] = useState(false);
  const [initialData, setInitialData] = useState<Partial<AgentFormData> | undefined>();

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

  const handleTemplateSelect = (config: {
    name: string;
    persona: string;
    model_config: { model: string };
    mode: string;
    tools: string[];
    skills: string[];
    knowledge_base_ids: string[];
    risk_level: string;
    opening_remarks: string;
    enable_suggestions: boolean;
  }) => {
    setInitialData({
      name: config.name,
      persona: config.persona,
      model: config.model_config.model,
      mode: config.mode,
      tools: config.tools,
      skills: config.skills,
      knowledge_base_ids: config.knowledge_base_ids,
      risk_level: config.risk_level,
      opening_remarks: config.opening_remarks,
      enable_suggestions: config.enable_suggestions,
    });
    setShowTemplatePicker(false);
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Create Agent</h1>
        <Button variant="outline" onClick={() => setShowTemplatePicker(true)}>
          <LayoutTemplate className="mr-1 h-4 w-4" />
          From Template
        </Button>
      </div>
      <AgentConfigurator
        initialData={initialData}
        onSubmit={handleSubmit}
        submitLabel="Create Agent"
      />
      {showTemplatePicker && (
        <TemplatePicker
          onSelect={handleTemplateSelect}
          onClose={() => setShowTemplatePicker(false)}
        />
      )}
    </div>
  );
}
