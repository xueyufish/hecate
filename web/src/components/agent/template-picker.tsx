"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { X, Headphones, Code, Search, PenTool, BarChart } from "lucide-react";

interface TemplatePreview {
  id: string;
  name: string;
  description: string;
  category: string;
  preview: {
    icon?: string;
    tags?: string[];
  };
}

interface TemplateConfig {
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
}

interface TemplatePickerProps {
  onSelect: (config: TemplateConfig) => void;
  onClose: () => void;
}

const iconMap: Record<string, React.ReactNode> = {
  headphones: <Headphones className="h-6 w-6" />,
  code: <Code className="h-6 w-6" />,
  search: <Search className="h-6 w-6" />,
  "pen-tool": <PenTool className="h-6 w-6" />,
  "bar-chart": <BarChart className="h-6 w-6" />,
};

export function TemplatePicker({ onSelect, onClose }: TemplatePickerProps) {
  const [templates, setTemplates] = useState<TemplatePreview[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<{ items: TemplatePreview[] }>("/api/agent-templates")
      .then((res) => setTemplates(res.items || []))
      .catch(() => setTemplates([]))
      .finally(() => setLoading(false));
  }, []);

  const handleSelect = async (templateId: string) => {
    try {
      const config = await api.post<TemplateConfig>(
        `/api/agent-templates/${templateId}/instantiate`,
        {},
      );
      onSelect(config);
    } catch (err: unknown) {
      const msg =
        err && typeof err === "object" && "error" in err
          ? (err as { error: { message: string } }).error.message
          : "Failed to load template";
      alert(msg);
    }
  };

  const categories = Array.from(new Set(templates.map((t) => t.category)));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="max-h-[80vh] w-full max-w-2xl overflow-y-auto rounded-lg bg-background p-6">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Select Template</h2>
          <Button variant="ghost" size="sm" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        {loading ? (
          <p className="text-center text-muted-foreground">Loading...</p>
        ) : templates.length === 0 ? (
          <p className="text-center text-muted-foreground">No templates available</p>
        ) : (
          <div className="space-y-6">
            {categories.map((category) => (
              <div key={category}>
                <h3 className="mb-2 text-sm font-medium capitalize">{category}</h3>
                <div className="grid grid-cols-2 gap-3">
                  {templates
                    .filter((t) => t.category === category)
                    .map((template) => (
                      <Card
                        key={template.id}
                        className="cursor-pointer transition-colors hover:border-primary"
                        onClick={() => handleSelect(template.id)}
                      >
                        <CardHeader className="pb-2">
                          <div className="flex items-center gap-2">
                            {iconMap[template.preview.icon || ""] || (
                              <div className="h-6 w-6 rounded bg-muted" />
                            )}
                            <CardTitle className="text-sm">{template.name}</CardTitle>
                          </div>
                        </CardHeader>
                        <CardContent>
                          <p className="text-xs text-muted-foreground line-clamp-2">
                            {template.description}
                          </p>
                          {template.preview.tags && (
                            <div className="mt-2 flex flex-wrap gap-1">
                              {template.preview.tags.map((tag) => (
                                <span
                                  key={tag}
                                  className="rounded bg-muted px-1.5 py-0.5 text-xs"
                                >
                                  {tag}
                                </span>
                              ))}
                            </div>
                          )}
                        </CardContent>
                      </Card>
                    ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
