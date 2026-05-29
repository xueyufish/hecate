"use client";

import { X } from "lucide-react";

interface ConfigPanelProps {
  node: {
    id: string;
    type: string;
    data: Record<string, unknown>;
  } | null;
  onUpdate: (nodeId: string, data: Record<string, unknown>) => void;
  onClose: () => void;
}

const TYPE_LABELS: Record<string, string> = {
  conversation: "对话节点",
  "tool-call": "工具调用节点",
  condition: "条件节点",
  agent: "Agent 节点",
  "knowledge-rerieval": "知识检索节点",
  "variable-set": "变量设置节点",
};

export function ConfigPanel({ node, onUpdate, onClose }: ConfigPanelProps) {
  if (!node) {
    return (
      <div className="flex h-full w-[300px] items-center justify-center border-l bg-muted/30 p-4 text-sm text-muted-foreground">
        选择节点以配置
      </div>
    );
  }

  const config = (node.data?.config || {}) as Record<string, unknown>;

  function handleChange(field: string, value: unknown) {
    const newConfig = { ...config, [field]: value };
    onUpdate(node!.id, { ...node!.data, config: newConfig });
  }

  return (
    <div className="flex h-full w-[300px] flex-col border-l bg-background">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <h3 className="text-sm font-semibold">
          {TYPE_LABELS[node.type] || "节点配置"}
        </h3>
        <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {node.type === "conversation" && (
          <>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                模型
              </label>
              <input
                type="text"
                className="w-full rounded-md border px-3 py-1.5 text-sm"
                value={(config.model as string) || ""}
                onChange={(e) => handleChange("model", e.target.value)}
                placeholder="gpt-4o"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                系统提示词
              </label>
              <textarea
                className="w-full rounded-md border px-3 py-1.5 text-sm"
                rows={4}
                value={(config.system_prompt as string) || ""}
                onChange={(e) => handleChange("system_prompt", e.target.value)}
                placeholder="输入系统提示词..."
              />
            </div>
          </>
        )}

        {node.type === "condition" && (
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              条件表达式
            </label>
            <input
              type="text"
              className="w-full rounded-md border px-3 py-1.5 text-sm"
              value={(config.expression as string) || ""}
              onChange={(e) => handleChange("expression", e.target.value)}
              placeholder="e.g. messages.length > 0"
            />
          </div>
        )}

        {node.type === "tool-call" && (
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              工具名称
            </label>
            <input
              type="text"
              className="w-full rounded-md border px-3 py-1.5 text-sm"
              value={(config.tool_name as string) || ""}
              onChange={(e) => handleChange("tool_name", e.target.value)}
              placeholder="tool_name"
            />
          </div>
        )}

        {node.type === "agent" && (
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Agent 引用
            </label>
            <input
              type="text"
              className="w-full rounded-md border px-3 py-1.5 text-sm"
              value={(config.agent_ref as string) || ""}
              onChange={(e) => handleChange("agent_ref", e.target.value)}
              placeholder="agent_id"
            />
          </div>
        )}

        {node.type === "knowledge-retrieval" && (
          <>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                知识库 ID（逗号分隔）
              </label>
              <input
                type="text"
                className="w-full rounded-md border px-3 py-1.5 text-sm"
                value={
                  Array.isArray(config.kb_ids)
                    ? (config.kb_ids as string[]).join(", ")
                    : ""
                }
                onChange={(e) =>
                  handleChange(
                    "kb_ids",
                    e.target.value
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean)
                  )
                }
                placeholder="kb-id-1, kb-id-2"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                查询模板
              </label>
              <textarea
                className="w-full rounded-md border px-3 py-1.5 text-sm"
                rows={3}
                value={(config.query_template as string) || ""}
                onChange={(e) => handleChange("query_template", e.target.value)}
                placeholder="查询模板..."
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Top K
              </label>
              <input
                type="number"
                className="w-full rounded-md border px-3 py-1.5 text-sm"
                value={(config.top_k as number) || 5}
                onChange={(e) => handleChange("top_k", Number(e.target.value))}
              />
            </div>
          </>
        )}

        {node.type === "variable-set" && (
          <>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                变量名
              </label>
              <input
                type="text"
                className="w-full rounded-md border px-3 py-1.5 text-sm"
                value={(config.variable_name as string) || ""}
                onChange={(e) => handleChange("variable_name", e.target.value)}
                placeholder="variable_name"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                值
              </label>
              <input
                type="text"
                className="w-full rounded-md border px-3 py-1.5 text-sm"
                value={
                  typeof config.value === "string" ? config.value : JSON.stringify(config.value) || ""
                }
                onChange={(e) => {
                  try {
                    handleChange("value", JSON.parse(e.target.value));
                  } catch {
                    handleChange("value", e.target.value);
                  }
                }}
                placeholder="值或 JSON 表达式"
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
