"use client";

import { useEffect, useState } from "react";
import { X, Radio, Wifi, Sparkles, Zap } from "lucide-react";
import { ChannelSelector } from "./channel-selector";
import { api } from "@/lib/api-client";

interface ConfigPanelProps {
  node: {
    id: string;
    type: string;
    data: Record<string, unknown>;
  } | null;
  onUpdate: (nodeId: string, data: Record<string, unknown>) => void;
  onClose: () => void;
  graphStateChannels?: string[];
  allNodes?: { id: string; type: string; data: Record<string, unknown> }[];
  edges?: { source: string; target: string }[];
}

interface AgentItem {
  id: string;
  name: string;
}

const TYPE_LABELS: Record<string, string> = {
  conversation: "Conversation Node",
  "tool-call": "Tool Call Node",
  condition: "Condition Node",
  agent: "Agent Node",
  "knowledge-retrieval": "Knowledge Retrieval Node",
  "variable-set": "Variable Set Node",
  "fan-out": "Fan-Out Node",
  merge: "Merge Node",
};

export function ConfigPanel({
  node,
  onUpdate,
  onClose,
  graphStateChannels = [],
  allNodes = [],
  edges = [],
}: ConfigPanelProps) {
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [agentsLoaded, setAgentsLoaded] = useState(false);

  useEffect(() => {
    api
      .get<{ items: AgentItem[]; total: number }>("/api/agents")
      .then((data) => {
        setAgents(data.items || []);
        setAgentsLoaded(true);
      })
      .catch(() => setAgentsLoaded(true));
  }, []);

  if (!node) {
    return (
      <div className="flex h-full w-[300px] items-center justify-center border-l bg-muted/30 p-4 text-sm text-muted-foreground">
        Select a node to configure
      </div>
    );
  }

  const config = (node.data?.config || {}) as Record<string, unknown>;
  const channels = (config.channels as { readable?: string[]; writable?: string[] }) || {};

  function handleChange(field: string, value: unknown) {
    const newConfig = { ...config, [field]: value };
    onUpdate(node!.id, { ...node!.data, config: newConfig });
  }

  function handleChannelsChange(updated: {
    readable: string[];
    writable: string[];
  }) {
    handleChange("channels", updated);
  }

  function handleModelOverride(value: string) {
    if (value) {
      handleChange("model", value);
    } else {
      const { model: _, ...rest } = config;
      onUpdate(node!.id, { ...node!.data, config: rest });
    }
  }

  return (
    <div className="flex h-full w-[300px] flex-col border-l bg-background">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <h3 className="text-sm font-semibold">
          {TYPE_LABELS[node.type] || "Node Configuration"}
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
                Model
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
                System Prompt
              </label>
              <textarea
                className="w-full rounded-md border px-3 py-1.5 text-sm"
                rows={4}
                value={(config.system_prompt as string) || ""}
                onChange={(e) => handleChange("system_prompt", e.target.value)}
                placeholder="Enter system prompt..."
              />
            </div>
          </>
        )}

        {node.type === "condition" && (
          <>
            {/* Routing Mode Selector */}
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Routing Mode
              </label>
              <div className="flex flex-col gap-1.5">
                <label className="flex items-center gap-1.5 text-xs">
                  <input
                    type="radio"
                    name="routing_mode"
                    value="condition"
                    checked={
                      !config.routing_mode ||
                      (config.routing_mode as string) === "condition"
                    }
                    onChange={() => {
                      const { routing_mode: _, routing_config: __, ...rest } = config;
                      onUpdate(node!.id, { ...node!.data, config: rest });
                    }}
                  />
                  <Zap className="h-3 w-3 text-amber-500" />
                  Condition
                  <span className="text-[10px] text-muted-foreground">
                    — expression-based
                  </span>
                </label>
                <label className="flex items-center gap-1.5 text-xs">
                  <input
                    type="radio"
                    name="routing_mode"
                    value="intent"
                    checked={(config.routing_mode as string) === "intent"}
                    onChange={() => {
                      handleChange("routing_mode", "intent");
                      if (!config.routing_config) {
                        handleChange("routing_config", { intent_patterns: [], routing_prompt: "" });
                      }
                    }}
                  />
                  <Radio className="h-3 w-3 text-blue-500" />
                  Intent
                  <span className="text-[10px] text-muted-foreground">
                    — regex + LLM
                  </span>
                </label>
                <label className="flex items-center gap-1.5 text-xs">
                  <input
                    type="radio"
                    name="routing_mode"
                    value="dynamic"
                    checked={(config.routing_mode as string) === "dynamic"}
                    onChange={() => {
                      handleChange("routing_mode", "dynamic");
                      if (!config.routing_config) {
                        handleChange("routing_config", {
                          candidate_agents: [],
                          routing_prompt: "",
                          allow_repeated_speaker: true,
                        });
                      }
                    }}
                  />
                  <Sparkles className="h-3 w-3 text-violet-500" />
                  Dynamic
                  <span className="text-[10px] text-muted-foreground">
                    — LLM selects speaker
                  </span>
                </label>
              </div>
            </div>

            {/* Condition Mode: Expression Field (default) */}
            {(!config.routing_mode || (config.routing_mode as string) === "condition") && (
              <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">
                  Condition Expression
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

            {/* Intent Mode: Pattern Rows + Routing Prompt */}
            {(config.routing_mode as string) === "intent" && (
              <>
                <div>
                  <label className="mb-1 block text-xs font-medium text-muted-foreground">
                    Intent Patterns
                  </label>
                  {(() => {
                    const routingConfig = (config.routing_config || {}) as Record<string, unknown>;
                    const patterns = (routingConfig.intent_patterns || []) as Array<{ pattern: string; target: string }>;
                    const agentNodes = allNodes.filter((n) => n.type === "agent");
                    return (
                      <div className="space-y-2">
                        {patterns.map((p, idx) => (
                          <div key={idx} className="flex gap-1">
                            <input
                              type="text"
                              className="flex-1 rounded-md border px-2 py-1 text-xs"
                              value={p.pattern}
                              onChange={(e) => {
                                const updated = [...patterns];
                                updated[idx] = { ...updated[idx], pattern: e.target.value };
                                handleChange("routing_config", { ...routingConfig, intent_patterns: updated });
                              }}
                              placeholder="regex pattern"
                            />
                            <select
                              className="w-24 rounded-md border px-1 py-1 text-xs"
                              value={p.target}
                              onChange={(e) => {
                                const updated = [...patterns];
                                updated[idx] = { ...updated[idx], target: e.target.value };
                                handleChange("routing_config", { ...routingConfig, intent_patterns: updated });
                              }}
                            >
                              <option value="">Target...</option>
                              {agentNodes.map((n) => (
                                <option key={n.id} value={n.id}>
                                  {(n.data?.label as string) || n.id}
                                </option>
                              ))}
                            </select>
                            <button
                              onClick={() => {
                                const updated = patterns.filter((_, i) => i !== idx);
                                handleChange("routing_config", { ...routingConfig, intent_patterns: updated });
                              }}
                              className="text-xs text-red-500 hover:text-red-700"
                            >
                              ✕
                            </button>
                          </div>
                        ))}
                        <button
                          onClick={() => {
                            const updated = [...patterns, { pattern: "", target: "" }];
                            handleChange("routing_config", { ...routingConfig, intent_patterns: updated });
                          }}
                          className="text-xs text-primary hover:underline"
                        >
                          + Add Pattern
                        </button>
                      </div>
                    );
                  })()}
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-muted-foreground">
                    Routing Prompt (LLM Fallback)
                  </label>
                  <textarea
                    className="w-full rounded-md border px-3 py-1.5 text-sm"
                    rows={3}
                    value={((config.routing_config as Record<string, unknown>)?.routing_prompt as string) || ""}
                    onChange={(e) => {
                      const rc = (config.routing_config || {}) as Record<string, unknown>;
                      handleChange("routing_config", { ...rc, routing_prompt: e.target.value });
                    }}
                    placeholder="Prompt to classify intent when no pattern matches..."
                  />
                </div>
              </>
            )}

            {/* Dynamic Mode: Candidate Agents + Routing Prompt + Toggle */}
            {(config.routing_mode as string) === "dynamic" && (
              <>
                <div>
                  <label className="mb-1 block text-xs font-medium text-muted-foreground">
                    Candidate Agents
                  </label>
                  {(() => {
                    const routingConfig = (config.routing_config || {}) as Record<string, unknown>;
                    const candidates = (routingConfig.candidate_agents || []) as string[];
                    const agentNodes = allNodes.filter((n) => n.type === "agent");
                    return (
                      <div className="space-y-1">
                        {agentNodes.map((n) => (
                          <label key={n.id} className="flex items-center gap-1.5 text-xs">
                            <input
                              type="checkbox"
                              checked={candidates.includes(n.id)}
                              onChange={(e) => {
                                const updated = e.target.checked
                                  ? [...candidates, n.id]
                                  : candidates.filter((c) => c !== n.id);
                                handleChange("routing_config", { ...routingConfig, candidate_agents: updated });
                              }}
                            />
                            {(n.data?.label as string) || n.id}
                          </label>
                        ))}
                        {agentNodes.length === 0 && (
                          <p className="text-xs text-muted-foreground italic">
                            No agent nodes on canvas
                          </p>
                        )}
                      </div>
                    );
                  })()}
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-muted-foreground">
                    Routing Prompt
                  </label>
                  <textarea
                    className="w-full rounded-md border px-3 py-1.5 text-sm"
                    rows={3}
                    value={((config.routing_config as Record<string, unknown>)?.routing_prompt as string) || ""}
                    onChange={(e) => {
                      const rc = (config.routing_config || {}) as Record<string, unknown>;
                      handleChange("routing_config", { ...rc, routing_prompt: e.target.value });
                    }}
                    placeholder="Prompt for LLM to select the best agent..."
                  />
                </div>
                <div>
                  <label className="flex items-center gap-1.5 text-xs">
                    <input
                      type="checkbox"
                      checked={
                        ((config.routing_config as Record<string, unknown>)?.allow_repeated_speaker as boolean) !== false
                      }
                      onChange={(e) => {
                        const rc = (config.routing_config || {}) as Record<string, unknown>;
                        handleChange("routing_config", { ...rc, allow_repeated_speaker: e.target.checked });
                      }}
                    />
                    Allow Repeated Speaker
                  </label>
                  <p className="mt-0.5 text-[10px] text-muted-foreground">
                    When disabled, the last speaker is excluded from candidates
                  </p>
                </div>
              </>
            )}
          </>
        )}

        {node.type === "tool-call" && (
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Tool Name
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
          <>
            {/* Agent Selector */}
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Agent
              </label>
              <select
                className="w-full rounded-md border px-3 py-1.5 text-sm"
                value={(config.agent_ref as string) || ""}
                onChange={(e) => handleChange("agent_ref", e.target.value)}
              >
                <option value="">Select agent...</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
              {!agentsLoaded && (
                <p className="mt-1 text-[10px] text-muted-foreground">
                  Loading agents...
                </p>
              )}
            </div>

            {/* Role Description */}
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Role Description
              </label>
              <textarea
                className="w-full rounded-md border px-3 py-1.5 text-sm"
                rows={3}
                value={(config.system_prompt as string) || ""}
                onChange={(e) => handleChange("system_prompt", e.target.value)}
                placeholder="Describe the agent's role..."
              />
            </div>

            {/* Invocation Mode */}
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Invocation Mode
              </label>
              <div className="flex gap-3">
                <label className="flex items-center gap-1.5 text-xs">
                  <input
                    type="radio"
                    name="invocation_mode"
                    value="direct"
                    checked={
                      (config.invocation_mode as string) !== "tool"
                    }
                    onChange={() => handleChange("invocation_mode", "direct")}
                  />
                  Direct
                </label>
                <label className="flex items-center gap-1.5 text-xs">
                  <input
                    type="radio"
                    name="invocation_mode"
                    value="tool"
                    checked={
                      (config.invocation_mode as string) === "tool"
                    }
                    onChange={() => handleChange("invocation_mode", "tool")}
                  />
                  Tool
                </label>
              </div>
            </div>

            {/* Channel Selector */}
            <ChannelSelector
              available={graphStateChannels}
              readable={channels.readable || []}
              writable={channels.writable || []}
              onChange={handleChannelsChange}
            />

            {/* Channel Access Summary */}
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Channel Access Summary
              </label>
              {(!channels.readable?.length && !channels.writable?.length) ? (
                <p className="text-xs text-muted-foreground italic">
                  No channel access configured
                </p>
              ) : (
                <div className="space-y-1.5">
                  {channels.readable?.length ? (
                    <div>
                      <p className="mb-0.5 text-[10px] font-medium text-muted-foreground">
                        Readable
                      </p>
                      {channels.readable.map((ch: string) => (
                        <div
                          key={ch}
                          className="flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs"
                        >
                          <Radio className="h-3 w-3 text-blue-500" />
                          <span>{ch}</span>
                          {graphStateChannels.includes(ch) && (
                            <Wifi className="ml-auto h-3 w-3 text-green-500" />
                          )}
                        </div>
                      ))}
                    </div>
                  ) : null}
                  {channels.writable?.length ? (
                    <div>
                      <p className="mb-0.5 text-[10px] font-medium text-muted-foreground">
                        Writable
                      </p>
                      {channels.writable.map((ch: string) => (
                        <div
                          key={ch}
                          className="flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs"
                        >
                          <Wifi className="h-3 w-3 text-amber-500" />
                          <span>{ch}</span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              )}
            </div>

            {/* Model Override */}
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Model Override
              </label>
              <input
                type="text"
                className="w-full rounded-md border px-3 py-1.5 text-sm"
                value={(config.model as string) || ""}
                onChange={(e) => handleModelOverride(e.target.value)}
                placeholder="Leave empty for default"
              />
            </div>
          </>
        )}

        {node.type === "fan-out" && (
          <>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Branch Count
              </label>
              <select
                className="w-full rounded-md border px-3 py-1.5 text-sm"
                value={
                  (config.branches as string[])?.length || 2
                }
                onChange={(e) => {
                  const count = Number(e.target.value);
                  const existing = (config.branches as string[]) || [];
                  const branches = existing.slice(0, count);
                  while (branches.length < count) {
                    branches.push(`branch_${branches.length + 1}`);
                  }
                  handleChange("branches", branches.slice(0, count));
                }}
              >
                {[2, 3, 4, 5, 6].map((n) => (
                  <option key={n} value={n}>
                    {n} branches
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Connected Branches
              </label>
              {(() => {
                const connectedTargets = edges
                  .filter((e) => e.source === node.id)
                  .map((e) => e.target);
                if (connectedTargets.length === 0) {
                  return (
                    <p className="text-xs text-amber-600">
                      No branches connected. Connect edges to target nodes.
                    </p>
                  );
                }
                return (
                  <div className="space-y-1">
                    {connectedTargets.map((targetId) => (
                      <div
                        key={targetId}
                        className="rounded border px-2 py-1 text-xs"
                      >
                        {targetId}
                      </div>
                    ))}
                  </div>
                );
              })()}
            </div>
          </>
        )}

        {node.type === "merge" && (
          <>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Fan-Out Source
              </label>
              {(() => {
                const fanOutNodes = allNodes.filter(
                  (n) => n.type === "fan-out"
                );
                if (fanOutNodes.length === 0) {
                  return (
                    <p className="text-xs text-amber-600">
                      No fan-out nodes on canvas. Add a fan-out node first.
                    </p>
                  );
                }
                return (
                  <select
                    className="w-full rounded-md border px-3 py-1.5 text-sm"
                    value={(config.fan_out_source as string) || ""}
                    onChange={(e) =>
                      handleChange("fan_out_source", e.target.value)
                    }
                  >
                    <option value="">Select fan-out source...</option>
                    {fanOutNodes.map((n) => (
                      <option key={n.id} value={n.id}>
                        {(n.data?.label as string) || n.id}
                      </option>
                    ))}
                  </select>
                );
              })()}
              {!(config.fan_out_source as string) && (
                <p className="mt-1 text-xs text-amber-600">
                  No fan-out source linked. Select a fan-out node as source.
                </p>
              )}
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Output Channel
              </label>
              <input
                type="text"
                className="w-full rounded-md border px-3 py-1.5 text-sm"
                value={(config.output_channel as string) || ""}
                onChange={(e) =>
                  handleChange("output_channel", e.target.value)
                }
                placeholder="e.g. analysis_results"
              />
            </div>
          </>
        )}

        {node.type === "knowledge-retrieval" && (
          <>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Knowledge Base IDs (comma-separated)
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
                Query Template
              </label>
              <textarea
                className="w-full rounded-md border px-3 py-1.5 text-sm"
                rows={3}
                value={(config.query_template as string) || ""}
                onChange={(e) => handleChange("query_template", e.target.value)}
                placeholder="Query template..."
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
                Variable Name
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
                Value
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
                placeholder="Value or JSON expression"
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
