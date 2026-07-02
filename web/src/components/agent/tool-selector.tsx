"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api-client";
import { MultiSelect, MultiSelectOption } from "@/components/ui/multi-select";
import { Label } from "@/components/ui/label";

interface Tool {
  id: string;
  name: string;
  description: string;
  type: string;
}

interface ToolSelectorProps {
  selected: string[];
  onChange: (selected: string[]) => void;
}

export function ToolSelector({ selected, onChange }: ToolSelectorProps) {
  const [options, setOptions] = useState<MultiSelectOption[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<{ items: Tool[] }>("/api/tools")
      .then((res) =>
        setOptions(
          (res.items || []).map((t) => ({
            id: t.id,
            label: t.name,
            description: t.description,
          }))
        )
      )
      .catch(() => setOptions([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-2">
      <Label>Tools</Label>
      <MultiSelect
        options={options}
        selected={selected}
        onChange={onChange}
        placeholder={loading ? "Loading..." : "Select tools..."}
        disabled={loading}
      />
      {options.length === 0 && !loading && (
        <p className="text-sm text-muted-foreground">
          No tools available. Configure MCP servers or add custom tools first.
        </p>
      )}
    </div>
  );
}
