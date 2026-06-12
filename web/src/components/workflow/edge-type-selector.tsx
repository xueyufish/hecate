"use client";

import { useState, useRef, useEffect } from "react";

interface EdgeTypeSelectorProps {
  position: { x: number; y: number };
  onSelect: (type: "default" | "handoff" | "conditional" | "dynamic_handoff", label?: string) => void;
  onCancel: () => void;
}

const EDGE_TYPES = [
  {
    type: "default" as const,
    label: "Default",
    description: "Solid gray",
    color: "#94a3b8",
    style: "solid",
  },
  {
    type: "handoff" as const,
    label: "Handoff",
    description: "Dashed purple",
    color: "#8b5cf6",
    style: "dashed",
  },
  {
    type: "conditional" as const,
    label: "Conditional",
    description: "Dotted amber",
    color: "#d97706",
    style: "dotted",
  },
  {
    type: "dynamic_handoff" as const,
    label: "Dynamic Handoff",
    description: "Dash-dot violet",
    color: "#7c3aed",
    style: "dashdot",
  },
];

export function EdgeTypeSelector({
  position,
  onSelect,
  onCancel,
}: EdgeTypeSelectorProps) {
  const [showLabelInput, setShowLabelInput] = useState(false);
  const [conditionLabel, setConditionLabel] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onCancel();
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [onCancel]);

  function handleSelect(type: "default" | "handoff" | "conditional" | "dynamic_handoff") {
    if (type === "conditional") {
      setShowLabelInput(true);
      return;
    }
    onSelect(type);
  }

  function handleLabelSubmit() {
    onSelect("conditional", conditionLabel || undefined);
  }

  return (
    <div
      ref={ref}
      className="absolute z-50 w-48 rounded-lg border bg-background p-2 shadow-lg"
      style={{ left: position.x, top: position.y }}
    >
      {!showLabelInput ? (
        <div className="space-y-1">
          <p className="text-[10px] font-medium text-muted-foreground">
            Edge Type
          </p>
          {EDGE_TYPES.map((et) => (
            <button
              key={et.type}
              onClick={() => handleSelect(et.type)}
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-muted"
            >
              <svg width="32" height="4" className="shrink-0">
                <line
                  x1="0"
                  y1="2"
                  x2="32"
                  y2="2"
                  stroke={et.color}
                  strokeWidth="2"
                  strokeDasharray={
                    et.style === "dashed"
                      ? "5 3"
                      : et.style === "dotted"
                        ? "2 4"
                        : et.style === "dashdot"
                          ? "6 2 2 2"
                          : "none"
                  }
                />
              </svg>
              <div className="flex flex-col items-start">
                <span className="font-medium">{et.label}</span>
                <span className="text-[10px] text-muted-foreground">
                  {et.description}
                </span>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-[10px] font-medium text-muted-foreground">
            Condition Label
          </p>
          <input
            type="text"
            className="w-full rounded-md border px-2 py-1 text-xs"
            value={conditionLabel}
            onChange={(e) => setConditionLabel(e.target.value)}
            placeholder="e.g. finance, tech..."
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter") handleLabelSubmit();
              if (e.key === "Escape") onCancel();
            }}
          />
          <div className="flex gap-1">
            <button
              onClick={handleLabelSubmit}
              className="flex-1 rounded-md bg-primary px-2 py-1 text-xs text-primary-foreground"
            >
              OK
            </button>
            <button
              onClick={() => setShowLabelInput(false)}
              className="flex-1 rounded-md border px-2 py-1 text-xs"
            >
              Back
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
