export interface ModelOption {
  id: string;
  provider?: string;
  provider_display_name?: string;
}

export interface ModelGroup {
  provider_name: string;
  provider_display_name: string;
  models: ModelOption[];
}

export function groupModelsByProvider(models: ModelOption[]): ModelGroup[] {
  const grouped: Record<string, ModelGroup> = {};
  for (const m of models) {
    const key = m.provider || "unknown";
    if (!grouped[key]) {
      grouped[key] = {
        provider_name: key,
        provider_display_name: m.provider_display_name || key,
        models: [],
      };
    }
    grouped[key].models.push(m);
  }
  return Object.values(grouped);
}
