// Tests for the settings/models page publish lifecycle (6.47) and
// management quick wins (6.48): badge derivation, publish-button gating,
// publish/unpublish calls, and search/filter wiring.

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ModelsPage from "@/app/(dashboard)/settings/models/page";

const mockApi = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  api: mockApi,
}));

const providersPayload = {
  items: [
    {
      id: "p1",
      name: "prov",
      display_name: "Prov",
      status: "active",
      is_enabled: true,
      model_count: 3,
      call_count_30d: 12,
      call_count_total: 40,
    },
  ],
  total: 1,
  unmatched_call_count_30d: 2,
  unmatched_call_count_total: 7,
};

const modelsPayload = {
  items: [
    {
      provider_id: "p1",
      provider_name: "prov",
      provider_display_name: "Prov",
      models: [
        {
          id: "m-pub",
          provider_id: "p1",
          model_id: "published-model",
          display_name: "Published Model",
          model_type: "chat",
          is_custom: false,
          is_enabled: true,
          is_published: true,
          last_test_passed_at: "2026-09-14T00:00:00Z",
        },
        {
          id: "m-tested",
          provider_id: "p1",
          model_id: "tested-model",
          display_name: "Tested Model",
          model_type: "chat",
          is_custom: false,
          is_enabled: true,
          is_published: false,
          last_test_passed_at: "2026-09-14T00:00:00Z",
        },
        {
          id: "m-unpub",
          provider_id: "p1",
          model_id: "unpublished-model",
          display_name: "Unpublished Model",
          model_type: "chat",
          is_custom: false,
          is_enabled: true,
          is_published: false,
          last_test_passed_at: null,
        },
      ],
    },
  ],
  total: 3,
};

function mockApiResponses() {
  mockApi.get.mockImplementation(async (url: string) => {
    if (url.startsWith("/api/model-providers")) return providersPayload;
    if (url.startsWith("/api/models")) return modelsPayload;
    throw new Error(`unexpected GET ${url}`);
  });
}

async function expandProvider() {
  mockApiResponses();
  render(<ModelsPage />);
  await screen.findByText("Prov");
  await userEvent.click(screen.getByRole("button", { name: "Toggle models for Prov" }));
  await screen.findByText("Published Model");
}

describe("ModelsPage publish lifecycle", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("renders the three-state publish badges", async () => {
    await expandProvider();

    // Query badges by their data-slot: the publish-state Select shares the
    // "Published"/"Unpublished" strings, so text queries are ambiguous.
    const badgeTexts = Array.from(document.querySelectorAll('[data-slot="badge"]')).map((el) => el.textContent);
    expect(badgeTexts).toContain("Published");
    expect(badgeTexts).toContain("Test passed");
    expect(badgeTexts).toContain("Unpublished");
  });

  it("gates the publish button on test evidence", async () => {
    await expandProvider();

    const publishButtons = screen.getAllByRole("button", { name: "Publish" });
    expect(publishButtons).toHaveLength(2); // tested + untested models

    const untested = publishButtons.find((b) => b.hasAttribute("disabled"));
    expect(untested).toBeDefined();
    expect(untested?.getAttribute("title")).toContain("test");

    const tested = publishButtons.find((b) => !b.hasAttribute("disabled"));
    expect(tested).toBeDefined();
  });

  it("calls publish / unpublish endpoints on click", async () => {
    mockApi.post.mockResolvedValue({});
    await expandProvider();

    const publishButtons = await screen.findAllByRole("button", { name: "Publish" });
    const enabled = publishButtons.find((b) => !b.hasAttribute("disabled"))!;
    await userEvent.click(enabled);
    await waitFor(() =>
      expect(mockApi.post).toHaveBeenCalledWith("/api/models/m-tested/publish", {})
    );

    const unpublishButton = screen.getByRole("button", { name: "Unpublish" });
    await userEvent.click(unpublishButton);
    await waitFor(() =>
      expect(mockApi.post).toHaveBeenCalledWith("/api/models/m-pub/unpublish", {})
    );
  });

  it("shows provider call counts and the unmatched bucket", async () => {
    await expandProvider();

    expect(screen.getByText("Calls")).toBeInTheDocument();
    expect(screen.getByText(/Unmatched calls/)).toBeInTheDocument();
  });

  it("wires the provider search box to a debounced refetch", async () => {
    mockApiResponses();
    render(<ModelsPage />);
    await screen.findByText("Prov");

    await userEvent.type(screen.getByPlaceholderText("Search providers…"), "pro");
    await waitFor(
      () => {
        expect(mockApi.get).toHaveBeenCalledWith(
          expect.stringContaining("/api/model-providers?search=pro")
        );
      },
      { timeout: 2000 }
    );
  });

  it("wires the model search box to a debounced refetch when expanded", async () => {
    await expandProvider();
    mockApi.get.mockClear();
    mockApi.get.mockImplementation(async (url: string) =>
      url.startsWith("/api/model-providers") ? providersPayload : modelsPayload
    );

    await userEvent.type(screen.getByPlaceholderText("Search models…"), "glm");
    await waitFor(
      () => {
        expect(mockApi.get).toHaveBeenCalledWith(
          expect.stringContaining("search=glm")
        );
      },
      { timeout: 2000 }
    );
  });
});
