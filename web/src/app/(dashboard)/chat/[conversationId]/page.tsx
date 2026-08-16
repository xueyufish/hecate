"use client";

import { useParams, useRouter } from "next/navigation";

import { ChatSurface } from "@/components/chat/ChatSurface";

export default function ChatPage() {
  const params = useParams();
  const router = useRouter();
  const conversationId = params.conversationId as string;

  const handleNewChat = (newConversationId: string) => {
    router.push(`/chat/${newConversationId}`);
  };

  return (
    <ChatSurface
      mode="dashboard"
      conversationId={conversationId}
      agentId=""
      onNewChat={handleNewChat}
    />
  );
}
