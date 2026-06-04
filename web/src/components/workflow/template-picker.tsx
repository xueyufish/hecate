"use client";

import { useEffect, useState } from "react";
import { LayoutTemplate } from "lucide-react";
import { api } from "@/lib/api-client";

interface TemplatePreview {
  total_nodes: number;
  agent_nodes: number;
  total_edges: number;
}

interface TemplateItem {
  id: string;
  name: string;
  description: string;
  category: string;
  preview: TemplatePreview;
}

interface TemplatePickerProps {
  onSelect: (template: Record<string, unknown>) => void;
  onClose: () => void;
}

export function TemplatePicker({ onSelect, onClose }: TemplatePickerProps) {
  const [templates, setTemplates] = useState<TemplateItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<{ items: TemplateItem[] }>("/api/orchestration-templates")
      .then((data) => setTemplates(data.items || []))
      .catch(() => setTemplates([]))
      .finally(() => setLoading(false));
  }, []);

  async function handleSelect(templateId: string) {
    try {
      const dsl = await api.get<Record<string, unknown>>(
        `/api/orchestration-templates/${templateId}`
      );
      onSelect(dsl);
    } catch {
      // Template load failed — ignore
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-[480px] rounded-lg border bg-background p-4 shadow-lg">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold">Templates</h3>
          <button
            onClick={onClose}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            Close
          </button>
        </div>

        {loading && (
          <p className="text-xs text-muted-foreground">Loading...</p>
        )}

        {!loading && templates.length === 0 && (
          <p className="text-xs text-muted-foreground">No templates available</p>
        )}

        <div className="space-y-2 max-h-[400px] overflow-y-auto">
          {templates.map((t) => (
            <button
              key={t.id}
              onClick={() => handleSelect(t.id)}
              className="w-full text-left rounded-md border p-3 hover:bg-muted transition-colors"
            >
              <div className="flex items-center gap-2">
                <LayoutTemplate className="h-4 w-4 text-violet-600" />
                <span className="text-sm font-medium">{t.name}</span>
                <span className="text-[10px] rounded bg-muted px-1.5 py-0.5 text-muted-foreground">
                  {t.category}
                </span>
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                {t.description}
              </p>
              <div className="flex gap-3 mt-1.5 text-[10px] text-muted-foreground">
                <span>{t.preview.total_nodes} nodes</span>
                <span>{t.preview.agent_nodes} Agent</span>
                <span>{t.preview.total_edges} edges</span>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
