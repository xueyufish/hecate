"use client";

import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";

export interface ConversationHeaderProps {
  agentId: string;
  conversationId: string | null;
  kbLoading: boolean;
  kbNames: string[];
  memoryBlockLabels: string[];
  queuePosition: number;
  onNewChat: () => void | Promise<void>;
}

export function ConversationHeader({
  kbLoading,
  kbNames,
  memoryBlockLabels,
  queuePosition,
  onNewChat,
}: ConversationHeaderProps) {
  return (
    <div className="flex items-center justify-between border-b px-4 py-3">
      <div className="flex items-center gap-2">
        <h2 className="text-lg font-medium">Chat</h2>
        {kbLoading && (
          <span className="text-xs text-muted-foreground">Loading knowledge base...</span>
        )}
        {!kbLoading && kbNames.length > 0 && (
          <div className="flex items-center gap-1">
            {kbNames.map((name, i) => (
              <span
                key={i}
                className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs"
              >
                {name}
              </span>
            ))}
          </div>
        )}
        {memoryBlockLabels.length > 0 && (
          <div className="flex items-center gap-1">
            {memoryBlockLabels.map((label, i) => (
              <span
                key={i}
                className="inline-flex items-center rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-800"
              >
                {label}
              </span>
            ))}
          </div>
        )}
        {queuePosition > 0 && (
          <span className="inline-flex items-center rounded-full bg-yellow-100 px-2 py-0.5 text-xs text-yellow-800">
            Queued...
          </span>
        )}
      </div>
      <Button
        variant="outline"
        size="sm"
        onClick={onNewChat}
        data-testid="chat-new-chat"
      >
        <Plus className="mr-1 h-4 w-4" />
        New Chat
      </Button>
    </div>
  );
}
