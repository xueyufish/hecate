"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Send, Plus } from "lucide-react";

interface Message {
  role: "user" | "assistant" | "tool";
  content: string;
  tool_calls?: { function: { name: string; arguments: string } }[];
  tool_call_id?: string;
  name?: string;
}

export default function ChatPage() {
  const params = useParams();
  const router = useRouter();
  const conversationId = params.conversationId as string;
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [agentModel, setAgentModel] = useState("gpt-4o");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api
      .get<{ messages?: Message[]; agent_id?: string }>(
        `/api/conversations/${conversationId}`
      )
      .then((conv) => {
        if (conv.messages) setMessages(conv.messages);
        if (conv.agent_id) {
          api
            .get<{ model_config: { model?: string } }>(
              `/api/agents/${conv.agent_id}`
            )
            .then((a) => setAgentModel(a.model_config?.model || "gpt-4o"))
            .catch(() => {});
        }
      })
      .catch(() => {});
  }, [conversationId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = async () => {
    if (!input.trim() || streaming) return;
    const userMsg: Message = { role: "user", content: input.trim() };
    const updated = [...messages, userMsg];
    setMessages(updated);
    setInput("");
    setStreaming(true);

    const assistantMsg: Message = { role: "assistant", content: "" };
    setMessages([...updated, assistantMsg]);

    try {
      for await (const token of api.stream("/v1/chat/completions", {
        model: agentModel,
        messages: updated.map((m) => ({ role: m.role, content: m.content })),
      })) {
        assistantMsg.content += token;
        setMessages([...updated, { ...assistantMsg }]);
      }
    } catch {
      assistantMsg.content += "\n\n[请求失败]";
      setMessages([...updated, { ...assistantMsg }]);
    } finally {
      setStreaming(false);
    }
  };

  const newChat = async () => {
    const agentId = await api
      .get<{ agent_id?: string }>(`/api/conversations/${conversationId}`)
      .then((c) => c.agent_id);
    if (!agentId) return;
    const conv = await api.post<{ id: string }>("/api/conversations", {
      agent_id: agentId,
    });
    router.push(`/chat/${conv.id}`);
  };

  return (
    <div className="flex h-[calc(100vh-3rem)] flex-col">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <h2 className="text-lg font-medium">对话</h2>
        <Button variant="outline" size="sm" onClick={newChat}>
          <Plus className="mr-1 h-4 w-4" />
          新对话
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        {messages.length === 0 && (
          <p className="text-center text-muted-foreground">
            输入消息开始对话
          </p>
        )}
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`mb-4 flex ${
              msg.role === "user" ? "justify-end" : "justify-start"
            }`}
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
                  工具: {msg.name}
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
                        调用工具: {tc.function.name}
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
            placeholder="输入消息..."
            disabled={streaming}
            className="flex-1"
          />
          <Button type="submit" disabled={streaming || !input.trim()}>
            <Send className="h-4 w-4" />
          </Button>
        </form>
      </div>
    </div>
  );
}
