"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api-client";
import { groupModelsByProvider, ModelGroup, ModelOption } from "@/lib/model-grouping";
import { Label } from "@/components/ui/label";
import { AlertTriangle } from "lucide-react";

interface ModelSelectorProps {
  value: string;
  onChange: (value: string) => void;
  showAvailability?: boolean;
}

export function ModelSelector({ value, onChange, showAvailability = false }: ModelSelectorProps) {
  const [groups, setGroups] = useState<ModelGroup[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<{ data: ModelOption[] }>("/v1/models")
      .then((res) => setGroups(groupModelsByProvider(res.data || [])))
      .catch(() => setGroups([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-2">
      <Label htmlFor="model">Model</Label>
      <select
        id="model"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required
        className="flex h-10 w-full rounded-md border bg-background px-3 py-2 text-sm"
      >
        <option value="">
          {loading ? "Loading..." : "Select model"}
        </option>
        {groups.length === 0 && !loading ? (
          <option value="" disabled>
            No models available. Configure a provider first.
          </option>
        ) : (
          groups.map((group) => (
            <optgroup
              key={group.provider_name}
              label={group.provider_display_name}
            >
              {group.models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.id}
                </option>
              ))}
            </optgroup>
          ))
        )}
      </select>
      {showAvailability && !value && groups.length > 0 && (
        <p className="text-sm text-muted-foreground flex items-center gap-1">
          <AlertTriangle className="h-3 w-3" />
          Select a model to continue
        </p>
      )}
    </div>
  );
}
