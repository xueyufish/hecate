import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";

import { ControllerConfigSection } from "../controller-config";

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
  });

  it("requires an intent package reference", async () => {
    setup({ category_targets: { billing: "a1" }, default_workflow: "a2" });
    expect(await screen.findByText(/intent package reference is required/i)).toBeTruthy();
  });

  it("requires a default workflow", async () => {
    setup({ intent_package: { package_id: "pkg-1" } });
    expect(await screen.findByText(/default workflow is required/i)).toBeTruthy();
  });

  it("renders mapping rows from existing category targets", async () => {
    setup({
      intent_package: { package_id: "pkg-1", version_id: "v2" },
      category_targets: { billing: "a1", tech: "a2" },
      default_workflow: "a2",
    });
    expect(await screen.findByText("billing")).toBeTruthy();
    expect(screen.getByText("tech")).toBeTruthy();
  });

  it("emits package reference changes through onChange", async () => {
    const { onChange } = setup({ default_workflow: "a2" });
    fireEvent.change(screen.getByPlaceholderText(/published package id/i), {
      target: { value: "pkg-9" },
    });
    expect(
      onChange.mock.calls.some(
        ([field, value]) =>
          field === "intent_package" &&
          (value as Record<string, unknown>).package_id === "pkg-9"
      )
    ).toBe(true);
  });

  it("clears the reference when the package id is emptied", async () => {
    const { onChange } = setup({
      intent_package: { package_id: "pkg-1", version_id: "v2" },
      default_workflow: "a2",
    });
    const input = screen.getByPlaceholderText(/published package id/i);
    await userEvent.clear(input);
    expect(
      onChange.mock.calls.some(([field, value]) => field === "intent_package" && value === null)
    ).toBe(true);
  });

  it("drops the old version pin when switching packages", () => {
    const { onChange } = setup({
      intent_package: { package_id: "pkg-1", version_id: "v2" },
      default_workflow: "a2",
    });
    const input = screen.getByPlaceholderText(/published package id/i);
    fireEvent.change(input, { target: { value: " pkg-1 " } });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: " pkg-9 " } });
    expect(onChange).toHaveBeenLastCalledWith("intent_package", { package_id: "pkg-9" });
  });

  it("sets or clears an explicit version pin", () => {
    const { onChange } = setup({
      intent_package: { package_id: "pkg-1", version_id: "v1" },
      default_workflow: "a2",
    });
    const input = screen.getByPlaceholderText(/optional published version id/i);
    fireEvent.change(input, { target: { value: "v2" } });
    expect(onChange).toHaveBeenLastCalledWith("intent_package", {
      package_id: "pkg-1",
      version_id: "v2",
    });
    fireEvent.change(input, { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith("intent_package", { package_id: "pkg-1" });
  });

  it("adds mappings through onChange", async () => {
    const { onChange } = setup({
      intent_package: { package_id: "pkg-1" },
      category_targets: {},
      default_workflow: "a2",
    });
    await userEvent.type(screen.getByPlaceholderText("category"), "billing");
    await userEvent.click(screen.getByText("add mapping"));
    expect(
      onChange.mock.calls.some(
        ([field, value]) =>
          field === "category_targets" &&
          (value as Record<string, string>).billing === ""
      )
    ).toBe(true);
  });

  it("lists workflow targets limited to declared nodes", async () => {
    setup({
      intent_package: { package_id: "pkg-1", version_id: "v2" },
      category_targets: { billing: "a1" },
      default_workflow: "a2",
    });
    await screen.findByText("billing");
    // Targets offered in selects are exactly the declared agent nodes.
    const selects = document.querySelectorAll("select");
    const options = Array.from(selects[selects.length - 1].options).map((o) => o.value);
    expect(options.filter(Boolean).sort()).toEqual(["a1", "a2"]);
  });
});
