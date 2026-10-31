// Task-level controlled-continuation panel: recorded facts only. The panel
// is a pure view over the task detail's continuation summary — no check is
// re-evaluated, no write affordance exists, absence reads as "no recorded
// information" (never the policy being off), a trigger never claims the
// follow-up model call completed, and reaching the bound never reads as a
// task failure.

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TaskContinuationPanel } from "./task-continuation-panel";
import type { TaskContinuationSummary } from "@/lib/task-types";

function attemptOf(overrides: Record<string, unknown> = {}) {
  return {
    attempt: 1,
    candidate_decision_step_id: "agent-turn-000002",
    candidate_digest: "f".repeat(64),
    status: "acceptance_failed",
    standard_version: 2,
    effective_turn: 3,
    failed_checks: [
      { kind: "content_contains", status: "failed", detail: "delivered content does not contain 'three items'" },
    ],
    ...overrides,
  };
}

function summaryOf(overrides: Record<string, unknown> = {}): TaskContinuationSummary {
  return {
    max_continuations: 1,
    attempts: [attemptOf()],
    ...overrides,
  } as TaskContinuationSummary;
}

describe("TaskContinuationPanel", () => {
  it("renders the bound, the recorded trigger count and one attempt's facts", () => {
    render(<TaskContinuationPanel continuation={summaryOf()} />);

    expect(screen.getByTestId("continuation-bound").textContent).toContain("续跑上限：1");
    expect(screen.getByTestId("continuation-triggered-count").textContent).toContain(
      "已记录触发次数：1",
    );
    const row = screen.getByTestId("continuation-attempt-row");
    expect(within(row).getByTestId("continuation-attempt-index").textContent).toContain("触发序号：1");
    expect(within(row).getByText("验收未通过")).toBeInTheDocument();
    expect(within(row).getByText("候选决策：agent-turn-000002")).toBeInTheDocument();
    // The whole digest stays available via title; the display is shortened.
    expect(within(row).getByText(`${"f".repeat(16)}…`)).toBeInTheDocument();
    expect(within(row).getByText("标准版本：2")).toBeInTheDocument();
    expect(within(row).getByText("生效回合：3")).toBeInTheDocument();
    const checks = within(row).getByTestId("continuation-failed-checks");
    expect(within(checks).getAllByTestId("continuation-check-row")).toHaveLength(1);
    expect(within(checks).getByText("content_contains")).toBeInTheDocument();
    expect(within(checks).getByText("未通过")).toBeInTheDocument();
    expect(within(checks).queryByText(/does not contain 'three items'/)).not.toBeInTheDocument();
  });

  it("renders multiple recorded attempts in order", () => {
    render(
      <TaskContinuationPanel
        continuation={summaryOf({
          max_continuations: 3,
          attempts: [
            attemptOf(),
            attemptOf({
              attempt: 2,
              candidate_decision_step_id: "agent-turn-000004",
              effective_turn: 5,
            }),
          ],
        })}
      />,
    );
    const rows = screen.getAllByTestId("continuation-attempt-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByTestId("continuation-attempt-index").textContent).toContain("1");
    expect(within(rows[1]).getByTestId("continuation-attempt-index").textContent).toContain("2");
    expect(within(rows[1]).getByText("候选决策：agent-turn-000004")).toBeInTheDocument();
    expect(screen.getByTestId("continuation-triggered-count").textContent).toContain(
      "已记录触发次数：2",
    );
  });

  it("reports absent summary as missing information, never as the policy being off", () => {
    render(<TaskContinuationPanel />);
    expect(screen.getByTestId("continuation-absent").textContent).toContain("无已记录的续跑信息");
    expect(screen.getByTestId("continuation-panel").textContent).toContain("不能据此推断续跑策略已关闭");
    expect(screen.queryByTestId("continuation-bound")).not.toBeInTheDocument();
    expect(screen.queryByTestId("continuation-triggered-count")).not.toBeInTheDocument();
    expect(screen.queryByTestId("continuation-bound-reached")).not.toBeInTheDocument();
  });

  it("shows an empty attempts list without claiming any trigger", () => {
    render(<TaskContinuationPanel continuation={summaryOf({ attempts: [] })} />);
    expect(screen.getByTestId("continuation-empty").textContent).toContain("无已记录的续跑触发");
    expect(screen.getByTestId("continuation-triggered-count").textContent).toContain(
      "已记录触发次数：0",
    );
    expect(screen.queryByTestId("continuation-attempts")).not.toBeInTheDocument();
    expect(screen.queryByTestId("continuation-bound-reached")).not.toBeInTheDocument();
  });

  it("marks the reached bound without displaying it as a task failure", () => {
    render(
      <TaskContinuationPanel
        continuation={summaryOf({
          max_continuations: 1,
          attempts: [attemptOf()],
        })}
      />,
    );
    const reached = screen.getByTestId("continuation-bound-reached");
    expect(reached.textContent).toContain("已记录触发次数达到续跑上限");
    expect(reached.textContent).toContain("不表示任务失败");
    expect(reached.textContent).not.toContain("任务终态以各 Run 的最终交付验收结论为准");
    // The trail never asserts a lifecycle verdict of its own.
    expect(screen.queryByText("失败")).not.toBeInTheDocument();
  });

  it("renders missing and unknown fields as recorded-unknown, never as success", () => {
    render(
      <TaskContinuationPanel
        continuation={summaryOf({
          max_continuations: null,
          attempts: [
            attemptOf({
              attempt: null,
              status: "some_new_verdict",
              candidate_decision_step_id: null,
              candidate_digest: null,
              standard_version: null,
              effective_turn: null,
              failed_checks: null,
            }),
          ],
        })}
      />,
    );
    expect(screen.getByTestId("continuation-bound").textContent).toContain("续跑上限：未记录");
    const row = screen.getByTestId("continuation-attempt-row");
    expect(within(row).getByTestId("continuation-attempt-index").textContent).toContain(
      "触发序号：未记录",
    );
    // An unknown verdict falls back to the raw string, never to a pass.
    expect(within(row).getByText("some_new_verdict")).toBeInTheDocument();
    expect(within(row).queryByText("验收通过")).not.toBeInTheDocument();
    expect(within(row).getByText("候选决策：未记录")).toBeInTheDocument();
    expect(within(row).getByText("标准版本：—")).toBeInTheDocument();
    expect(within(row).getByText("生效回合：—")).toBeInTheDocument();
    expect(within(row).queryByTestId("continuation-failed-checks")).not.toBeInTheDocument();
  });

  it("stays read-only: no buttons, inputs or submit affordances", () => {
    const { container } = render(<TaskContinuationPanel continuation={summaryOf()} />);
    expect(container.querySelector("button, input, textarea, form")).toBeNull();
  });

  it.each([undefined, null])("does not turn a missing attempt list (%s) into a zero count", (attempts) => {
    render(<TaskContinuationPanel continuation={summaryOf({ attempts })} />);
    expect(screen.getByTestId("continuation-triggered-count")).toHaveTextContent("已记录触发次数：未记录");
    expect(screen.queryByTestId("continuation-empty")).not.toBeInTheDocument();
    expect(screen.queryByTestId("continuation-bound-reached")).not.toBeInTheDocument();
  });

  it.each([0, -1, 0.5])("does not infer a reached bound from an invalid counter (%s)", (value) => {
    render(<TaskContinuationPanel continuation={summaryOf({
      max_continuations: value,
      attempts: [attemptOf({ attempt: value, standard_version: value, effective_turn: value })],
    })} />);
    expect(screen.getByTestId("continuation-bound")).toHaveTextContent("续跑上限：未记录");
    expect(screen.queryByTestId("continuation-bound-reached")).not.toBeInTheDocument();
    const row = screen.getByTestId("continuation-attempt-row");
    expect(within(row).getByTestId("continuation-attempt-index")).toHaveTextContent("触发序号：未记录");
    expect(within(row).getByText("标准版本：—")).toBeInTheDocument();
    expect(within(row).getByText("生效回合：—")).toBeInTheDocument();
  });

  it("excludes internal feedback and diagnostics from the rendered receipt", () => {
    const diagnostic = "internal-check-diagnostic: private-value";
    const feedback = "internal-feedback: private-instruction";
    const { container } = render(<TaskContinuationPanel continuation={summaryOf({ attempts: [attemptOf({
      feedback,
      failed_checks: [{ kind: "content_contains", status: "failed", detail: diagnostic }],
    })] })} />);
    expect(screen.getByText("content_contains")).toBeInTheDocument();
    expect(screen.getByText("未通过")).toBeInTheDocument();
    expect(container.innerHTML).not.toContain(diagnostic);
    expect(container.innerHTML).not.toContain(feedback);
  });

  it("keeps the task-level scope note next to the trail", () => {
    render(<TaskContinuationPanel continuation={summaryOf()} />);
    expect(screen.getByTestId("continuation-scope-note").textContent).toContain("任务级记录");
    expect(screen.getByTestId("continuation-scope-note").textContent).toContain("分别呈现");
    expect(screen.getByTestId("continuation-count-note").textContent).toContain(
      "不代表总回合数、成本或剩余预算",
    );
    expect(screen.getByTestId("continuation-count-note").textContent).toContain(
      "不能证明后续模型调用已完成",
    );
  });
});
