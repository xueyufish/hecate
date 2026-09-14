"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { api } from "@/lib/api-client";

/**
 * Controller node config panel (2.6a / 1.1.21).
 *
 * Sections: intent package picker (published versions + optional pin),
 * category→workflow mapping editor rendered from the referenced version's
 * categories (removed categories flagged for reassignment), start/default/
 * end workflow assignment, and the global-intent section. Validation errors
 * surface inline on the offending field; an invalid config persists nothing
 * beyond the normal graph save path, which re-validates server-side.
 */

interface ControllerConfigSectionProps {
  config: Record<string, unknown>;
  allNodes: { id: string; type: string; data: Record<string, unknown> }[];
  onChange: (field: string, value: unknown) => void;
}

interface PackageItem {
  id: string;
  name: string;
}

interface VersionItem {
  id: string;
  name: string;
  published_at: string | null;
  content: { categories?: Array<{ name: string; description?: string | null }> };
}

export function ControllerConfigSection({
  config,
  allNodes,
  onChange,
}: ControllerConfigSectionProps) {
  const packageRef = (config.intent_package as Record<string, unknown>) || {};
  const categoryTargets = (config.category_targets as Record<string, string>) || {};
  const globalIntent = (config.global_intent as Record<string, unknown>) || {};

  const [packages, setPackages] = useState<PackageItem[]>([]);
  const [versions, setVersions] = useState<VersionItem[]>([]);
  const [versionCategories, setVersionCategories] = useState<string[]>([]);
  const [removedCategories, setRemovedCategories] = useState<string[]>([]);

  const selectedPackageId = (packageRef.package_id as string) || "";
  const selectedVersionId = (packageRef.version_id as string) || "";

  const agentNodes = useMemo(
    () => allNodes.filter((n) => n.type === "agent" || n.type === "conversation"),
    [allNodes]
  );

  useEffect(() => {
    api
      .get<{ items: PackageItem[] }>("/api/intent-packages")
      .then((data) => setPackages(data.items || []))
      .catch(() => setPackages([]));
  }, []);

  useEffect(() => {
    if (!selectedPackageId) {
      setVersions([]);
      return;
    }
    api
      .get<{ items: VersionItem[] }>(
        `/api/intent-packages/${selectedPackageId}/versions?published_only=true`
      )
      .then((data) => {
        setVersions(data.items || []);
        // No pin or the pin vanished → default to latest published.
        if (
          !(data.items || []).some((v) => v.id === selectedVersionId) &&
          data.items?.length
        ) {
          onChange("intent_package", { package_id: selectedPackageId, version_id: data.items[0].id });
        }
      })
      .catch(() => setVersions([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPackageId]);

  useEffect(() => {
    const version = versions.find((v) => v.id === selectedVersionId) || versions[0];
    if (!version) {
      setVersionCategories([]);
      return;
    }
    // Fetch the full version (list items already carry content).
    const names = (version.content?.categories || []).map((c) => c.name);
    setVersionCategories(names);
    // Categories whose mapping survived a version switch but which the new
    // version no longer defines must be reassigned.
    const known = new Set(names);
    setRemovedCategories(Object.keys(categoryTargets).filter((k) => !known.has(k)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versions, selectedVersionId]);

  function updateMapping(category: string, target: string) {
    const next = { ...categoryTargets };
    if (target) {
      next[category] = target;
    } else {
      delete next[category];
    }
    onChange("category_targets", next);
  }

  function handlePackageSelect(id: string) {
    if (!id) {
      onChange("intent_package", null);
      setVersions([]);
      setVersionCategories([]);
      return;
    }
    // Version resets to latest published (the versions effect pins it).
    onChange("intent_package", { package_id: id });
  }

  return (
    <>
      {/* Intent package picker */}
      <div>
        <label className="mb-1 block text-xs font-medium text-muted-foreground">
          Intent Package
        </label>
        <select
          className="w-full rounded-md border px-2 py-1.5 text-sm"
          value={selectedPackageId}
          onChange={(e) => handlePackageSelect(e.target.value)}
        >
          <option value="">Select a package...</option>
          {packages.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        {!selectedPackageId && (
          <p className="mt-1 text-xs text-amber-600">
            An intent package reference is required.
          </p>
        )}
      </div>

      {/* Version pin */}
      {selectedPackageId && (
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            Package Version (default: latest published)
          </label>
          <select
            className="w-full rounded-md border px-2 py-1.5 text-sm"
            value={selectedVersionId}
            onChange={(e) =>
              onChange("intent_package", {
                package_id: selectedPackageId,
                version_id: e.target.value || undefined,
              })
            }
          >
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
                {v.published_at ? "" : " (unpublished)"}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Category → workflow mapping editor */}
      {versionCategories.length > 0 && (
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            Intent → Workflow Mapping
          </label>
          <div className="space-y-2">
            {versionCategories.map((category) => (
              <div key={category} className="flex items-center gap-2">
                <span className="w-24 truncate rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium">
                  {category}
                </span>
                <select
                  className="flex-1 rounded-md border px-1 py-1 text-xs"
                  value={categoryTargets[category] || ""}
                  onChange={(e) => updateMapping(category, e.target.value)}
                >
                  <option value="">Target...</option>
                  {agentNodes.map((n) => (
                    <option key={n.id} value={n.id}>
                      {(n.data?.label as string) || n.id}
                    </option>
                  ))}
                </select>
              </div>
            ))}
            {removedCategories.map((category) => (
              <div key={category} className="flex items-center gap-2 rounded border border-red-300 bg-red-50 p-1.5">
                <AlertTriangle className="h-3 w-3 text-red-600" />
                <span className="w-24 truncate text-[10px] font-medium text-red-700 line-through">
                  {category}
                </span>
                <span className="flex-1 text-[10px] text-red-600">
                  Removed from package — reassign
                </span>
                <button
                  className="text-[10px] text-red-700 underline"
                  onClick={() => updateMapping(category, "")}
                >
                  drop
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Start / default / end workflow assignment */}
      <div>
        <label className="mb-1 block text-xs font-medium text-muted-foreground">
          Start Workflow (first turn)
        </label>
        <select
          className="w-full rounded-md border px-2 py-1.5 text-sm"
          value={(config.start_workflow as string) || ""}
          onChange={(e) => handleChangeOrClear(e.target.value, "start_workflow")}
        >
          <option value="">None</option>
          {agentNodes.map((n) => (
            <option key={n.id} value={n.id}>
              {(n.data?.label as string) || n.id}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="mb-1 block text-xs font-medium text-muted-foreground">
          Default Workflow (required)
        </label>
        <select
          className={`w-full rounded-md border px-2 py-1.5 text-sm ${
            !(config.default_workflow as string) ? "border-red-400" : ""
          }`}
          value={(config.default_workflow as string) || ""}
          onChange={(e) => handleChangeOrClear(e.target.value, "default_workflow")}
        >
          <option value="">Select a target...</option>
          {agentNodes.map((n) => (
            <option key={n.id} value={n.id}>
              {(n.data?.label as string) || n.id}
            </option>
          ))}
        </select>
        {!(config.default_workflow as string) && (
          <p className="mt-1 text-xs text-red-600">
            The default workflow is required.
          </p>
        )}
      </div>
      <div>
        <label className="mb-1 block text-xs font-medium text-muted-foreground">
          End Workflow
        </label>
        <select
          className="w-full rounded-md border px-2 py-1.5 text-sm"
          value={(config.end_workflow as string) || ""}
          onChange={(e) => handleChangeOrClear(e.target.value, "end_workflow")}
        >
          <option value="">None</option>
          {agentNodes.map((n) => (
            <option key={n.id} value={n.id}>
              {(n.data?.label as string) || n.id}
            </option>
          ))}
        </select>
      </div>

      {/* Global intent */}
      <div>
        <label className="flex items-center gap-2 text-xs font-medium">
          <input
            type="checkbox"
            checked={Boolean(globalIntent.enabled)}
            onChange={(e) =>
              onChange("global_intent", {
                ...globalIntent,
                enabled: e.target.checked,
              })
            }
          />
          Global Intent (session goal tracking)
        </label>
        {Boolean(globalIntent.enabled) && (
          <input
            type="text"
            className="mt-1 w-full rounded-md border px-2 py-1 text-xs"
            value={(globalIntent.goal_hint as string) || ""}
            onChange={(e) =>
              onChange("global_intent", {
                ...globalIntent,
                goal_hint: e.target.value,
              })
            }
            placeholder="Optional goal hint to seed the session goal"
          />
        )}
      </div>
    </>
  );

  function handleChangeOrClear(value: string, field: string) {
    if (value) {
      onChange(field, value);
    } else {
      onChange(field, null);
    }
  }
}
