"use client";

import { useState, useEffect } from "react";
import { api } from "@/lib/api-client";
import type { PatternDefinition } from "@/lib/workflow-types";
import {
  ArrowRight,
  GitFork,
  Handshake,
  Megaphone,
  Scale,
  Swords,
} from "lucide-react";

const PATTERN_ICONS: Record<string, React.ElementType> = {
  sequential: ArrowRight,
  parallel: GitFork,
  handoff: Handshake,
  broadcast: Megaphone,
  negotiation: Scale,
  debate: Swords,
};

const PATTERN_COLORS: Record<string, string> = {
  sequential: "border-blue-200 bg-blue-50 hover:bg-blue-100",
  parallel: "border-indigo-200 bg-indigo-50 hover:bg-indigo-100",
  handoff: "border-purple-200 bg-purple-50 hover:bg-purple-100",
  broadcast: "border-green-200 bg-green-50 hover:bg-green-100",
  negotiation: "border-amber-200 bg-amber-50 hover:bg-amber-100",
  debate: "border-red-200 bg-red-50 hover:bg-red-100",
};

const ICON_COLORS: Record<string, string> = {
  sequential: "text-blue-600",
  parallel: "text-indigo-600",
  handoff: "text-purple-600",
  broadcast: "text-green-600",
  negotiation: "text-amber-600",
  debate: "text-red-600",
};

interface PatternSelectorProps {
  open: boolean;
  onSelect: (pattern: PatternDefinition) => void;
  onClose: () => void;
}

export function PatternSelector({ open, onSelect, onClose }: PatternSelectorProps) {
  const [patterns, setPatterns] = useState<PatternDefinition[]>([]);

  useEffect(() => {
    if (open) {
      api
        .get<{ items: PatternDefinition[] }>("/api/collaboration-patterns")
        .then((data) => setPatterns(data.items))
        .catch(() => setPatterns([]));
    }
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-2xl rounded-lg bg-white p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Select Collaboration Pattern</h2>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-gray-400 hover:text-gray-600"
          >
            ✕
          </button>
        </div>
        <p className="mb-4 text-sm text-gray-500">
          Choose a pattern to auto-generate the workflow graph structure.
        </p>
        <div className="grid grid-cols-3 gap-3">
          {patterns.map((pattern) => {
            const Icon = PATTERN_ICONS[pattern.id] || ArrowRight;
            const borderColor = PATTERN_COLORS[pattern.id] || "border-gray-200 bg-gray-50";
            const iconColor = ICON_COLORS[pattern.id] || "text-gray-600";

            return (
              <button
                key={pattern.id}
                onClick={() => onSelect(pattern)}
                className={`rounded-lg border p-4 text-left transition-colors ${borderColor}`}
              >
                <div className="mb-2 flex items-center gap-2">
                  <Icon className={`h-5 w-5 ${iconColor}`} />
                  <span className="text-sm font-medium">{pattern.name}</span>
                </div>
                <p className="mb-2 text-xs text-gray-500 line-clamp-2">
                  {pattern.description}
                </p>
                <span className="text-xs text-gray-400">
                  {pattern.preview.min_nodes}+ nodes
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
