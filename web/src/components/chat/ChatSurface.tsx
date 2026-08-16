"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api-client";

import { ConversationHeader } from "./ConversationHeader";

export interface ChatMessage {
  role: "user" | "assistant" | "tool";
  content: string;
  tool_calls?: { function: { name: string; arguments: string } }[];
  tool_call_id?: string;
  name?: string;
}

export type ChatSurfaceMode = "dashboard" | "embed";

export interface ChatSurfaceProps {
  mode: ChatSurfaceMode;
  conversationId: string | null;
  agentId: string;
  onNewChat?: (newConversationId: string) => void;
}

export function ChatSurface({ mode, conversationId, agentId, onNewChat }: ChatSurfaceProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [resolvedAgentId, setResolvedAgentId] = useState<string>(agentId);
  const [agentModel, setAgentModel] = useState("gpt-4o");
  const [kbIds, setKbIds] = useState<string[]>([]);
  const [kbNames, setKbNames] = useState<string[]>([]);
  const [kbLoading, setKbLoading] = useState(false);
  const [memoryBlockLabels, setMemoryBlockLabels] = useState<string[]>([]);
  const [queuePosition, setQueuePosition] = useState(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (agentId) setResolvedAgentId(agentId);
  }, [agentId]);

  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;
    api
      .get<{ messages?: ChatMessage[]; agent_id?: string }>(
        `/api/conversations/${conversationId}`
      )
      .then((conv) => {
        if (cancelled) return;
        if (conv.messages) setMessages(conv.messages);
        if (conv.agent_id) {
          if (!agentId) setResolvedAgentId(conv.agent_id);
          api
            .get<{ model_config: { model?: string }; knowledge_base_ids?: string[] }>(
              `/api/agents/${conv.agent_id}`
            )
            .then((a) => {
              if (cancelled) return;
              setAgentModel(a.model_config?.model || "gpt-4o");
              const ids = a.knowledge_base_ids || [];
              setKbIds(ids);
              if (ids.length > 0) {
                setKbLoading(true);
                api
                  .get<{ items: { id: string; name: string }[] }>("/api/knowledge-bases")
                  .then((res) => {
                    if (cancelled) return;
                    const nameMap = new Map(res.items.map((kb) => [kb.id, kb.name]));
                    setKbNames(ids.map((id) => nameMap.get(id) || id).filter(Boolean));
                  })
                  .catch(() => {})
                  .finally(() => {
                    if (!cancelled) setKbLoading(false);
                  });
              }
              api
                .get<{ label: string }[]>(`/api/agents/${conv.agent_id}/memory-blocks`)
                .then((blocks) => {
                  if (!cancelled) setMemoryBlockLabels(blocks.map((b) => b.label));
                })
                .catch(() => {});
            })
            .catch(() => {});
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [conversationId, agentId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = async () => {
    if (!input.trim() || streaming || !conversationId) return;
    const userMsg: ChatMessage = { role: "user", content: input.trim() };
    const updated = [...messages, userMsg];
    setMessages(updated);
    setInput("");
    setStreaming(true);
    setQueuePosition(1);

    const assistantMsg: ChatMessage = { role: "assistant", content: "" };
    const activeMessages = [...updated, assistantMsg];
    setMessages(activeMessages);

    try {
      let firstChunk = false;
      for await (const token of api.stream("/v1/chat/completions", {
        model: agentModel,
        messages: updated.map((m) => ({ role: m.role, content: m.content })),
        session_id: conversationId,
        ...(kbIds.length > 0 ? { kb_ids: kbIds } : {}),
      })) {
        if (!firstChunk) {
          firstChunk = true;
          setQueuePosition(0);
        }
        assistantMsg.content += token;
        setMessages([...activeMessages.slice(0, -1), { ...assistantMsg }]);
      }
    } catch {
      assistantMsg.content += "\n\n[Request failed]";
      setMessages([...activeMessages.slice(0, -1), { ...assistantMsg }]);
    } finally {
      setStreaming(false);
      setQueuePosition(0);
    }
  };

  const handleNewChat = async () => {
    if (!resolvedAgentId) return;
    const conv = await api.post<{ id: string }>("/api/conversations", {
      agent_id: resolvedAgentId,
    });
    setMessages([]);
    setInput("");
    onNewChat?.(conv.id);
  };

  const containerClassName =
    mode === "dashboard"
      ? "flex h-[calc(100vh-3rem)] flex-col"
      : "flex h-full flex-col";

  return (
    <div className={containerClassName} data-testid="chat-surface" data-mode={mode}>
      <ConversationHeader
        agentId={agentId}
        conversationId={conversationId}
        kbLoading={kbLoading}
        kbNames={kbNames}
        memoryBlockLabels={memoryBlockLabels}
        queuePosition={queuePosition}
        onNewChat={handleNewChat}
      />

      <div className="flex-1 overflow-y-auto px-4 py-4">
        {messages.length === 0 && (
          <p className="text-center text-muted-foreground" data-testid="chat-empty-state">
            Type a message to start chatting
          </p>
        )}
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`mb-4 flex ${
              msg.role === "user" ? "justify-end" : "justify-start"
            }`}
            data-testid={`chat-message-${msg.role}`}
          >
            <div
              className={`max-w-[70%] rounded-lg px-4 py-2 ${
                msg.role === "user"
                  ? "bg-foreground text-background"
                  : "bg-muted"
              }`}
            >
              {msg.role === "tool" && msg.name && (
                <div className="mb-1 text-xs font-medium text-muted-foreground">
                  Tool: {msg.name}
                </div>
              )}
              <div className="whitespace-pre-wrap text-sm">
                {msg.content || (streaming && i === messages.length - 1 ? "..." : "")}
              </div>
              {msg.tool_calls && msg.tool_calls.length > 0 && (
                <div className="mt-2 space-y-1">
                  {msg.tool_calls.map((tc, j) => (
                    <details key={j} className="text-xs">
                      <summary className="cursor-pointer font-medium">
                        Calling tool: {tc.function.name}
                      </summary>
                      <pre className="mt-1 overflow-auto rounded bg-background/50 p-2">
                        {tc.function.arguments}
                      </pre>
                    </details>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <div className="border-t px-4 py-3">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            sendMessage();
          }}
          className="flex gap-2"
        >
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Type a message..."
            disabled={streaming}
            className="flex-1"
            data-testid="chat-input"
          />
          <Button type="submit" disabled={streaming || !input.trim()} data-testid="chat-send">
            Send
          </Button>
        </form>
      </div>
    </div>
  );
}
