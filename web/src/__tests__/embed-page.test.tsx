import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const searchParamsValue: { current: URLSearchParams } = { current: new URLSearchParams("agent=agent-abc") };

vi.mock("next/navigation", () => ({
  useSearchParams: () => searchParamsValue.current,
  useRouter: () => ({
    replace: vi.fn(),
    push: vi.fn(),
  }),
}));

vi.mock("@/components/auth-guard", () => ({
  AuthGuard: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    isAuthenticated: true,
    isLoading: false,
    userEmail: "test@example.com",
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
  }),
}));

vi.mock("@/lib/api-client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    stream: vi.fn(),
  },
}));

import { api } from "@/lib/api-client";

import EmbedChatRoute from "../app/embed/chat/page";

const mockedApi = vi.mocked(api);

describe("/embed/chat route", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    searchParamsValue.current = new URLSearchParams("agent=agent-abc");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the collapsed bubble by default and not the chat window", async () => {
    mockedApi.post.mockResolvedValue({ id: "created-conv" });

    render(<EmbedChatRoute />);

    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalledWith("/api/conversations", {
        agent_id: "agent-abc",
      });
    });

    expect(screen.getByTestId("widget-bubble-button")).toBeInTheDocument();
    expect(screen.queryByTestId("widget-bubble-window")).not.toBeInTheDocument();
  });

  it("expands to the chat window when the bubble is clicked", async () => {
    mockedApi.post.mockResolvedValue({ id: "created-conv" });
    mockedApi.get.mockResolvedValue({ messages: [], agent_id: "agent-abc" });

    const user = userEvent.setup();
    render(<EmbedChatRoute />);

    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalled();
    });

    await user.click(screen.getByTestId("widget-bubble-button"));

    expect(screen.getByTestId("widget-bubble-window")).toBeInTheDocument();
  });

  it("renders the no-agent placeholder when the agent query is missing and does not call the API", () => {
    searchParamsValue.current = new URLSearchParams();

    render(<EmbedChatRoute />);

    expect(screen.getByTestId("embed-no-agent-placeholder")).toBeInTheDocument();
    expect(mockedApi.post).not.toHaveBeenCalled();
  });
});
