"use client";

import {
  Bot,
  GitBranch,
  Wrench,
  Users,
  Search,
  Variable,
} from "lucide-react";

const PALETTE_ITEMS = [
  { type: "conversation", label: "对话", icon: Bot, color: "text-blue-600" },
  { type: "condition", label: "条件", icon: GitBranch, color: "text-yellow-600" },
  { type: "tool-call", label: "工具调用", icon: Wrench, color: "text-purple-600" },
  { type: "agent", label: "Agent", icon: Users, color: "text-green-600" },
  {
    type: "knowledge-retrieval",
    label: "知识检索",
    icon: Search,
    color: "text-cyan-600",
  },
  {
    type: "variable-set",
    label: "变量设置",
    icon: Variable,
    color: "text-orange-600",
  },
];

export function NodePalette() {
  function onDragStart(e: React.DragEvent, nodeType: string) {
    e.dataTransfer.setData("application/reactflow", nodeType);
    e.dataTransfer.effectAllowed = "move";
  }

  return (
    <>
      <div className="border-b px-3 py-2">
        <span className="text-xs font-semibold text-muted-foreground">
          节点类型
        </span>
      </div>
      <div className="flex-1 space-y-1 p-2">
        {PALETTE_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <div
              key={item.type}
              draggable
              onDragStart={(e) => onDragStart(e, item.type)}
              className="flex cursor-grab items-center gap-2 rounded-md border bg-background px-3 py-2 text-sm hover:bg-muted active:cursor-grabbing"
            >
              <Icon className={`h-4 w-4 ${item.color}`} />
              <span>{item.label}</span>
            </div>
          );
        })}
      </div>
    </>
  );
}
