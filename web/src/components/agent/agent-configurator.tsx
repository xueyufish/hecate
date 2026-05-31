"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ModelSelector } from "./model-selector";
import { KnowledgeSelector } from "./knowledge-selector";
import { SkillSelector } from "./skill-selector";
import { ToolSelector } from "./tool-selector";
import { MemoryBlockEditor } from "./memory-block-editor";

export interface AgentFormData {
  name: string;
  persona: string;
  model: string;
  mode: string;
  tools: string[];
  skills: string[];
  knowledge_base_ids: string[];
  risk_level: string;
  opening_remarks: string;
  enable_suggestions: boolean;
}

interface AgentConfiguratorProps {
  initialData?: Partial<AgentFormData>;
  onSubmit: (data: AgentFormData) => Promise<void>;
  submitLabel?: string;
  agentId?: string;
}

const TABS = ["Basic", "Knowledge", "Tools", "Memory", "Advanced"] as const;

export function AgentConfigurator({
  initialData,
  onSubmit,
  submitLabel = "Save",
  agentId,
}: AgentConfiguratorProps) {
  const [activeTab, setActiveTab] = useState<(typeof TABS)[number]>("Basic");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<AgentFormData>({
    name: initialData?.name || "",
    persona: initialData?.persona || "",
    model: initialData?.model || "",
    mode: initialData?.mode || "chat",
    tools: initialData?.tools || [],
    skills: initialData?.skills || [],
    knowledge_base_ids: initialData?.knowledge_base_ids || [],
    risk_level: initialData?.risk_level || "LOW",
    opening_remarks: initialData?.opening_remarks || "",
    enable_suggestions: initialData?.enable_suggestions ?? true,
  });

  const updateField = <K extends keyof AgentFormData>(
    key: K,
    value: AgentFormData[K]
  ) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name || !form.model) {
      setError("Name and model are required");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await onSubmit(form);
    } catch (err: unknown) {
      if (err && typeof err === "object" && "error" in err) {
        const apiErr = err as { error: { code: string; message: string; details: unknown } };
        setError(apiErr.error.message);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("Failed to save agent");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </div>
      )}

      <div className="flex gap-2 border-b">
        {TABS.map((tab) => (
          <button
            key={tab}
            type="button"
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === tab
                ? "border-b-2 border-primary text-primary"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {activeTab === "Basic" && (
        <Card>
          <CardHeader>
            <CardTitle>Basic Configuration</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="name">Name *</Label>
              <Input
                id="name"
                value={form.name}
                onChange={(e) => updateField("name", e.target.value)}
                required
                placeholder="Give your agent a name"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="persona">System Prompt</Label>
              <Textarea
                id="persona"
                value={form.persona}
                onChange={(e) => updateField("persona", e.target.value)}
                rows={6}
                placeholder="You are a helpful assistant that..."
              />
            </div>
            <ModelSelector
              value={form.model}
              onChange={(v) => updateField("model", v)}
            />
            <div className="space-y-2">
              <Label htmlFor="mode">Mode</Label>
              <select
                id="mode"
                value={form.mode}
                onChange={(e) => updateField("mode", e.target.value)}
                className="flex h-10 w-full rounded-md border bg-background px-3 py-2 text-sm"
              >
                <option value="chat">Chat</option>
                <option value="three_layer">Three Layer (Guard → Planner → Sub-Agent)</option>
              </select>
            </div>
          </CardContent>
        </Card>
      )}

      {activeTab === "Knowledge" && (
        <Card>
          <CardHeader>
            <CardTitle>Knowledge & Skills</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <KnowledgeSelector
              selected={form.knowledge_base_ids}
              onChange={(v) => updateField("knowledge_base_ids", v)}
            />
            <SkillSelector
              selected={form.skills}
              onChange={(v) => updateField("skills", v)}
            />
          </CardContent>
        </Card>
      )}

      {activeTab === "Tools" && (
        <Card>
          <CardHeader>
            <CardTitle>Tools</CardTitle>
          </CardHeader>
          <CardContent>
            <ToolSelector
              selected={form.tools}
              onChange={(v) => updateField("tools", v)}
            />
          </CardContent>
        </Card>
      )}

      {activeTab === "Memory" && (
        <Card>
          <CardHeader>
            <CardTitle>Memory Blocks</CardTitle>
          </CardHeader>
          <CardContent>
            {agentId ? (
              <MemoryBlockEditor agentId={agentId} />
            ) : (
              <p className="text-sm text-muted-foreground">
                Save the agent first, then add memory blocks.
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {activeTab === "Advanced" && (
        <Card>
          <CardHeader>
            <CardTitle>Advanced Settings</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="risk_level">Risk Level</Label>
              <select
                id="risk_level"
                value={form.risk_level}
                onChange={(e) => updateField("risk_level", e.target.value)}
                className="flex h-10 w-full rounded-md border bg-background px-3 py-2 text-sm"
              >
                <option value="LOW">Low</option>
                <option value="MEDIUM">Medium</option>
                <option value="HIGH">High</option>
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="opening_remarks">Opening Remarks</Label>
              <Textarea
                id="opening_remarks"
                value={form.opening_remarks}
                onChange={(e) => updateField("opening_remarks", e.target.value)}
                rows={3}
                placeholder="Welcome! I'm your assistant..."
              />
              <p className="text-xs text-muted-foreground">
                Static greeting shown when a conversation starts. Leave empty for AI-generated greeting.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="enable_suggestions"
                checked={form.enable_suggestions}
                onChange={(e) => updateField("enable_suggestions", e.target.checked)}
                className="h-4 w-4 rounded border"
              />
              <Label htmlFor="enable_suggestions">Enable follow-up suggestions</Label>
            </div>
          </CardContent>
        </Card>
      )}

      <div className="flex justify-end">
        <Button type="submit" disabled={loading}>
          {loading ? "Saving..." : submitLabel}
        </Button>
      </div>
    </form>
  );
}
