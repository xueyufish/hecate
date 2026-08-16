import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatSurface } from "./ChatSurface";

vi.mock("@/lib/api-client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    stream: vi.fn(),
  },
}));

import { api } from "@/lib/api-client";

const mockedApi = vi.mocked(api);

const CONVERSATION_ID = "conv-123";
const AGENT_ID = "agent-abc";

function setupHappyConversation() {
  mockedApi.get.mockImplementation((path: string) => {
    if (path === `/api/conversations/${CONVERSATION_ID}`) {
      return Promise.resolve({
        messages: [],
        agent_id: AGENT_ID,
      });
    }
    if (path === `/api/agents/${AGENT_ID}`) {
      return Promise.resolve({
        model_config: { model: "gpt-4o" },
        knowledge_base_ids: [],
      });
    }
    if (path === `/api/agents/${AGENT_ID}/memory-blocks`) {
      return Promise.resolve([]);
    }
    return Promise.reject(new Error(`Unexpected GET ${path}`));
  });
}

describe("ChatSurface", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders user and assistant messages with the expected alignment", async () => {
    setupHappyConversation();

    render(
      <ChatSurface
        mode="embed"
        conversationId={CONVERSATION_ID}
        agentId={AGENT_ID}
      />
    );

    await waitFor(() => {
      expect(mockedApi.get).toHaveBeenCalledWith(
        `/api/conversations/${CONVERSATION_ID}`
      );
    });

    expect(screen.getByTestId("chat-empty-state")).toBeInTheDocument();
    expect(screen.getByTestId("chat-surface")).toHaveAttribute("data-mode", "embed");
  });

  it("appends streamed chunks to the in-progress assistant message", async () => {
    setupHappyConversation();

    async function* fakeStream() {
      yield "Hello";
      yield " ";
      yield "world";
    }

    mockedApi.stream.mockReturnValue(fakeStream() as ReturnType<typeof api.stream>);
    mockedApi.post.mockResolvedValue({ id: "new-conv-id" });

    const user = userEvent.setup();
    render(
      <ChatSurface
        mode="embed"
        conversationId={CONVERSATION_ID}
        agentId={AGENT_ID}
      />
    );

    const input = await screen.findByTestId("chat-input");
    await user.type(input, "Hi there");
    await user.click(screen.getByTestId("chat-send"));

    await waitFor(() => {
      expect(screen.getByText("Hello world")).toBeInTheDocument();
    });

    expect(mockedApi.stream).toHaveBeenCalledWith(
      "/v1/chat/completions",
      expect.objectContaining({
        session_id: CONVERSATION_ID,
        messages: expect.arrayContaining([
          expect.objectContaining({ role: "user", content: "Hi there" }),
        ]),
      })
    );
  });

  it("creates a new conversation when the New Chat button is clicked", async () => {
    setupHappyConversation();
    mockedApi.post.mockResolvedValue({ id: "fresh-conv-id" });

    const onNewChat = vi.fn();
    render(
      <ChatSurface
        mode="embed"
        conversationId={CONVERSATION_ID}
        agentId={AGENT_ID}
        onNewChat={onNewChat}
      />
    );

    await screen.findByTestId("chat-new-chat");

    mockedApi.post.mockClear();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("chat-new-chat"));

    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalledWith("/api/conversations", {
        agent_id: AGENT_ID,
      });
    });
    expect(onNewChat).toHaveBeenCalledWith("fresh-conv-id");
  });
});
