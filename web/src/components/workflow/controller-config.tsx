"use client";

import { useMemo, useState } from "react";

/**
 * Controller node config panel (2.6a / 1.1.21).
 *
 * Sections: intent package reference (manual IDs — the intent-package
 * management API was retired in M1-T1, so references resolve to already
 * published versions server-side), category→workflow mapping editor,
 * start/default/end workflow assignment, and the global-intent section.
 * Validation errors surface inline on the offending field; an invalid
 * config persists nothing beyond the normal graph save path, which
 * re-validates server-side.
 */

interface ControllerConfigSectionProps {
  config: Record<string, unknown>;
  allNodes: { id: string; type: string; data: Record<string, unknown> }[];
  onChange: (field: string, value: unknown) => void;
}

export function ControllerConfigSection({
  config,
  allNodes,
  onChange,
}: ControllerConfigSectionProps) {
  const packageRef = (config.intent_package as Record<string, unknown>) || {};
  const categoryTargets = (config.category_targets as Record<string, string>) || {};
  const globalIntent = (config.global_intent as Record<string, unknown>) || {};

  const [newCategory, setNewCategory] = useState("");

  const selectedPackageId = (packageRef.package_id as string) || "";
  const selectedVersionId = (packageRef.version_id as string) || "";

  const agentNodes = useMemo(
    () => allNodes.filter((n) => n.type === "agent" || n.type === "conversation"),
    [allNodes]
  );

  function updateMapping(category: string, target: string) {
    const next = { ...categoryTargets };
    if (target) {
      next[category] = target;
    } else {
      delete next[category];
    }
    onChange("category_targets", next);
  }

  function addMapping() {
    const category = newCategory.trim();
    if (!category || categoryTargets[category] !== undefined) {
      return;
    }
    setNewCategory("");
    onChange("category_targets", { ...categoryTargets, [category]: "" });
  }

  return (
    <>
      {/* Intent package reference (manual IDs) */}
      <div>
        <label className="mb-1 block text-xs font-medium text-muted-foreground">
          Intent Package ID
        </label>
        <input
          type="text"
          className="w-full rounded-md border px-2 py-1.5 text-sm"
          value={selectedPackageId}
          onChange={(e) => {
            const packageId = e.target.value.trim();
            if (packageId !== selectedPackageId) {
              onChange("intent_package", packageId ? { package_id: packageId } : null);
            }
          }}
          placeholder="Published package ID"
        />
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
            Package Version ID (default: latest published)
          </label>
          <input
            type="text"
            className="w-full rounded-md border px-2 py-1.5 text-sm"
            value={selectedVersionId}
            onChange={(e) => {
              const versionId = e.target.value.trim();
              onChange("intent_package", versionId
                ? { package_id: selectedPackageId, version_id: versionId }
                : { package_id: selectedPackageId });
            }}
            placeholder="Optional published version ID"
          />
        </div>
      )}

      {/* Category → workflow mapping editor */}
      <div>
        <label className="mb-1 block text-xs font-medium text-muted-foreground">
          Intent → Workflow Mapping
        </label>
        <div className="space-y-2">
          {Object.keys(categoryTargets).map((category) => (
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
              <button
                className="text-[10px] text-muted-foreground underline"
                onClick={() => updateMapping(category, "")}
              >
                drop
              </button>
            </div>
          ))}
          <div className="flex items-center gap-2">
            <input
              type="text"
              className="w-24 rounded-md border px-1.5 py-1 text-[10px]"
              value={newCategory}
              onChange={(e) => setNewCategory(e.target.value)}
              placeholder="category"
            />
            <button
              className="rounded-md border px-2 py-1 text-[10px] font-medium"
              onClick={addMapping}
            >
              add mapping
            </button>
          </div>
        </div>
        <p className="mt-1 text-[10px] text-muted-foreground">
          Category names are validated against the referenced published
          version on save.
        </p>
      </div>

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
