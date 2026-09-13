import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { api } = vi.hoisted(() => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), stream: vi.fn() },
}));

vi.mock("@/lib/api-client", () => ({ api }));

import { ControllerConfigSection } from "../controller-config";

const PACKAGES = { items: [{ id: "pkg-1", name: "billing-intents" }] };
const VERSIONS = {
  items: [
    {
      id: "v2",
      name: "v2",
      published_at: "2026-09-13T00:00:00Z",
      content: { categories: [{ name: "billing" }, { name: "tech" }, { name: "hr" }] },
    },
    {
      id: "v1",
      name: "v1",
      published_at: "2026-09-12T00:00:00Z",
      content: { categories: [{ name: "billing" }] },
    },
  ],
};

const NODES = [
  { id: "a1", type: "agent", data: { label: "Billing Agent", config: {} } },
  { id: "a2", type: "agent", data: { label: "Fallback Agent", config: {} } },
];

function setup(config: Record<string, unknown>) {
  const onChange = vi.fn();
  render(
    <ControllerConfigSection config={config} allNodes={NODES} onChange={onChange} />
  );
  return { onChange };
}

describe("ControllerConfigSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.get.mockImplementation((url: string) => {
      if (url === "/api/intent-packages") return Promise.resolve(PACKAGES);
      if (url.includes("/versions")) return Promise.resolve(VERSIONS);
      return Promise.reject(new Error(`unexpected ${url}`));
    });
  });

  it("requires an intent package reference", async () => {
    setup({ category_targets: { billing: "a1" }, default_workflow: "a2" });
    expect(await screen.findByText(/intent package reference is required/i)).toBeTruthy();
  });

  it("requires a default workflow", async () => {
    setup({ intent_package: { package_id: "pkg-1" } });
    expect(await screen.findByText(/default workflow is required/i)).toBeTruthy();
  });

  it("renders mapping rows from the selected version categories", async () => {
    setup({
      intent_package: { package_id: "pkg-1", version_id: "v2" },
      category_targets: { billing: "a1" },
      default_workflow: "a2",
    });
    await waitFor(() => expect(screen.getByText("billing")).toBeTruthy());
    expect(screen.getByText("tech")).toBeTruthy();
    expect(screen.getByText("hr")).toBeTruthy();
  });

  it("flags mapping categories removed from the package version", async () => {
    setup({
      intent_package: { package_id: "pkg-1", version_id: "v2" },
      category_targets: { billing: "a1", legacy: "a1" },
      default_workflow: "a2",
    });
    await waitFor(() => expect(screen.getByText(/removed from package/i)).toBeTruthy());
    expect(screen.getByText("legacy")).toBeTruthy();
  });

  it("emits version pin changes through onChange", async () => {
    const { onChange } = setup({
      intent_package: { package_id: "pkg-1" },
      default_workflow: "a2",
    });
    // The versions effect pins the latest published version.
    await waitFor(() =>
      expect(
        onChange.mock.calls.some(
          ([field, value]) =>
            field === "intent_package" &&
            (value as Record<string, unknown>).version_id === "v2"
        )
      ).toBe(true)
    );
  });

  it("lists workflow targets limited to declared nodes", async () => {
    setup({
      intent_package: { package_id: "pkg-1", version_id: "v2" },
      default_workflow: "a2",
    });
    await screen.findByText("billing");
    // Targets offered in selects are exactly the declared agent nodes.
    const selects = document.querySelectorAll("select");
    const options = Array.from(selects[selects.length - 1].options).map((o) => o.value);
    expect(options.filter(Boolean).sort()).toEqual(["a1", "a2"]);
  });
});
