/**
 * 11.2 Web Widget (Simplified) — embed entry route.
 *
 * Architectural note: this route does NOT register a ChannelABC adapter and does
 * NOT route through the Gateway. The browser talks directly to /v1/chat/completions
 * and /api/conversations. This is intentional — see ADR-031.
 */
"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

import { AuthGuard } from "@/components/auth-guard";
import { ChatSurface } from "@/components/chat/ChatSurface";
import { WidgetBubble } from "@/components/chat/WidgetBubble";
import { api } from "@/lib/api-client";

function EmbedChatPage() {
  const searchParams = useSearchParams();
  const agentParam = searchParams.get("agent");

  const [conversationId, setConversationId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!agentParam) {
      setConversationId(null);
      return;
    }
    let cancelled = false;
    api
      .post<{ id: string }>("/api/conversations", { agent_id: agentParam })
      .then((conv) => {
        if (cancelled) return;
        setConversationId(conv.id);
      })
      .catch(() => {
        if (cancelled) return;
        setError("Failed to create conversation");
      });
    return () => {
      cancelled = true;
    };
  }, [agentParam]);

  if (!agentParam) {
    return (
      <WidgetBubble
        agentId=""
        conversationId={null}
        surface={
          <div
            className="flex h-full items-center justify-center p-4 text-center text-sm text-muted-foreground"
            data-testid="embed-no-agent-placeholder"
          >
            <p>
              No agent specified. Append{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-xs">?agent=&lt;uuid&gt;</code>{" "}
              to the URL.
            </p>
          </div>
        }
      />
    );
  }

  if (error) {
    return (
      <WidgetBubble
        agentId={agentParam}
        conversationId={null}
        surface={
          <div className="flex h-full items-center justify-center p-4 text-center text-sm text-destructive">
            {error}
          </div>
        }
      />
    );
  }

  if (!conversationId) {
    return (
      <WidgetBubble
        agentId={agentParam}
        conversationId={null}
        surface={
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            Loading conversation...
          </div>
        }
      />
    );
  }

  return (
    <WidgetBubble
      agentId={agentParam}
      conversationId={conversationId}
      surface={
        <ChatSurface
          mode="embed"
          conversationId={conversationId}
          agentId={agentParam}
        />
      }
    />
  );
}

export default function EmbedChatRoute() {
  return (
    <AuthGuard>
      <EmbedChatPage />
    </AuthGuard>
  );
}
