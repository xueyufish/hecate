// Run-level delivery/acceptance panel: recorded facts only. The panel is a
// pure view over the task detail's per-run summary — no re-evaluation, no
// digest recomputation from the preview, and absence of a record reads as
// "no record", never as a verdict.

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RunDeliveryPanel } from "./run-delivery-panel";
import type { RunSummary } from "@/lib/task-types";

function runOf(overrides: Partial<RunSummary> = {}, projection: Record<string, unknown> = {}): RunSummary {
  return {
    run_id: "run-1",
    attempt_no: 1,
    deployment_id: "dep-1",
    backend_ref: {},
    projection: {
      state: "succeeded",
      result_preview: '{"answer": "three items in stock"}',
      ...projection,
    },
    event_cursor: 3,
    ...overrides,
  };
}

const PASSED_VIEW = {
  status: "passed" as const,
  version: 3,
  declaration_sha256: "d".repeat(64),
  checker_version: "delivery-acceptance/1",
  content_sha256: "c".repeat(64),
  record_sha256: "r".repeat(64),
  checks: [
    { kind: "output_schema", status: "passed" },
    { kind: "content_contains", status: "passed" },
  ],
  final_decision: { decision_step_id: "agent-turn-000002", content_digest: "f".repeat(64) },
  evaluated_by: { host: "platform", workspace_id: "ws-1", submitted_by: "user-1" },
};

describe("RunDeliveryPanel", () => {
  it("renders the recorded verdict with whitelisted metadata and checks", () => {
    render(<RunDeliveryPanel run={runOf({ delivery_acceptance: PASSED_VIEW })} />);

    const acceptance = screen.getByTestId("delivery-acceptance-record");
    expect(within(acceptance).getByText("验收通过")).toBeInTheDocument();
    expect(within(acceptance).getByText("标准版本：3")).toBeInTheDocument();
    expect(within(acceptance).getByText("checker 版本：delivery-acceptance/1")).toBeInTheDocument();
    expect(within(acceptance).getByText("责任主体：platform（user-1）")).toBeInTheDocument();
    expect(within(acceptance).getByText("最终决策：agent-turn-000002")).toBeInTheDocument();
    const rows = within(acceptance).getAllByTestId("delivery-check-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("output_schema")).toBeInTheDocument();
    expect(within(rows[0]).getByText("通过")).toBeInTheDocument();
    // The whole digest stays available via title; the display is shortened.
    expect(within(acceptance).getByText(`${"d".repeat(16)}…`)).toBeInTheDocument();
  });

  it("shows the execution state and stop category separately from the verdict", () => {
    render(
      <RunDeliveryPanel
        run={runOf(
          { delivery_acceptance: { ...PASSED_VIEW, status: "acceptance_failed" } },
          { state: "failed", error: "delivered content does not contain 'three items'", error_category: "acceptance_failed" },
        )}
      />,
    );
    expect(screen.getByTestId("delivery-execution").textContent).toContain("失败");
    expect(screen.getByTestId("delivery-error-category").textContent).toContain("acceptance_failed");
    expect(screen.getByTestId("delivery-error").textContent).toContain("three items");
    expect(screen.getByText("验收未通过")).toBeInTheDocument();
  });

  it("marks a truncated preview without offering the preview as verification", () => {
    render(
      <RunDeliveryPanel
        run={runOf(
          { delivery_acceptance: { ...PASSED_VIEW, checks: [{ kind: "output_schema", status: "passed" }] } },
          { result_preview: "x".repeat(2000), result_preview_truncated: true },
        )}
      />,
    );
    expect(screen.getByTestId("delivery-preview-truncated").textContent).toContain("已截断");
  });

  it("reports no record as absence, never as a verdict", () => {
    render(<RunDeliveryPanel run={runOf()} />);
    expect(screen.getByTestId("delivery-no-record").textContent).toContain("无验收记录");
    expect(screen.queryByText("未声明验收")).not.toBeInTheDocument();
  });

  it("keeps an unavailable required check separate from success or failure", () => {
    render(
      <RunDeliveryPanel
        run={runOf({ delivery_acceptance: {
          ...PASSED_VIEW,
          status: "acceptance_unverified",
          checks: [{ kind: "output_schema", status: "unverified", detail: "Required check could not be verified." }],
        } })}
      />,
    );
    expect(screen.getByText("验收未能核实")).toBeInTheDocument();
    expect(screen.getByText("未能核实")).toBeInTheDocument();
    expect(screen.queryByText("验收通过")).not.toBeInTheDocument();
  });

  it("explains not_declared as recorded without a standard", () => {
    render(<RunDeliveryPanel run={runOf({ delivery_acceptance: { status: "not_declared", checker_version: "delivery-acceptance/1", checks: [] } })} />);
    const acceptance = screen.getByTestId("delivery-acceptance-record");
    expect(within(acceptance).getByText("未声明验收")).toBeInTheDocument();
    expect(within(acceptance).getByText(/不构成业务目标验收/)).toBeInTheDocument();
  });

  it("never displays an uninterpretable record as passed", () => {
    render(
      <RunDeliveryPanel
        run={runOf({
          delivery_acceptance: {
            status: "uninterpretable",
            reason: "stored acceptance record is corrupt or uses an unavailable checker version",
            recorded_status: "passed",
            checker_version: "delivery-acceptance/99",
          },
        })}
      />,
    );
    const acceptance = screen.getByTestId("delivery-acceptance-record");
    expect(within(acceptance).getByText("记录无法解释")).toBeInTheDocument();
    expect(
      within(acceptance).getByTestId("delivery-uninterpretable-reason").textContent,
    ).toContain("corrupt or uses an unavailable checker version");
    expect(within(acceptance).getByText("checker 版本：delivery-acceptance/99")).toBeInTheDocument();
    expect(within(acceptance).getByText(/原记录状态：验收通过/)).toBeInTheDocument();
  });

  it("handles a run with no recorded delivery preview", () => {
    render(<RunDeliveryPanel run={runOf({}, { state: "failed", result_preview: null })} />);
    expect(screen.getByTestId("delivery-preview-empty").textContent).toContain("无已记录的交付内容预览");
  });
});
