"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api-client";
import { MultiSelect, MultiSelectOption } from "@/components/ui/multi-select";
import { Label } from "@/components/ui/label";

interface Skill {
  id: string;
  name: string;
  description: string;
  provider?: string | null;
  trust_tier?: string;
}

interface SkillSelectorProps {
  selected: string[];
  onChange: (selected: string[]) => void;
}

export function SkillSelector({ selected, onChange }: SkillSelectorProps) {
  const [options, setOptions] = useState<MultiSelectOption[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<{ items: Skill[] }>("/api/skills")
      .then((res) =>
        setOptions(
          (res.items || []).map((s) => {
            // Provider registry (5.9-enh): surface origin + trust tier so
            // operators can tell bundled/user/project skills apart.
            const badge = s.provider ? `[${s.provider}${s.trust_tier ? ` · ${s.trust_tier}` : ""}] ` : "";
            return {
              id: s.id,
              label: s.name,
              description: `${badge}${s.description}`,
            };
          })
        )
      )
      .catch(() => setOptions([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-2">
      <Label>Skills</Label>
      <MultiSelect
        options={options}
        selected={selected}
        onChange={onChange}
        placeholder={loading ? "Loading..." : "Select skills..."}
        disabled={loading}
      />
      {options.length === 0 && !loading && (
        <p className="text-sm text-muted-foreground">
          No skills available. Create one first.
        </p>
      )}
    </div>
  );
}
