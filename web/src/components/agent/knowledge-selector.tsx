"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api-client";
import { MultiSelect, MultiSelectOption } from "@/components/ui/multi-select";
import { Label } from "@/components/ui/label";

interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
}

interface KnowledgeSelectorProps {
  selected: string[];
  onChange: (selected: string[]) => void;
  error?: string | null;
}

export function KnowledgeSelector({ selected, onChange, error }: KnowledgeSelectorProps) {
  const [options, setOptions] = useState<MultiSelectOption[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<{ items: KnowledgeBase[] }>("/api/knowledge-bases")
      .then((res) =>
        setOptions(
          (res.items || []).map((kb) => ({
            id: kb.id,
            label: kb.name,
            description: kb.description,
          }))
        )
      )
      .catch(() => setOptions([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-2">
      <Label>Knowledge Bases</Label>
      <MultiSelect
        options={options}
        selected={selected}
        onChange={onChange}
        placeholder={loading ? "Loading..." : "Select knowledge bases..."}
        disabled={loading}
      />
      {options.length === 0 && !loading && (
        <p className="text-sm text-muted-foreground">
          No knowledge bases available. Create one first.
        </p>
      )}
      {error && (
        <p className="text-sm text-red-600">{error}</p>
      )}
    </div>
  );
}
