// TaskChildObservationPanel unit coverage: the recorded-fact branches and
// the local navigation-link validation matrix. A relationship reference is
// navigable ONLY with the right kind, the platform's own issuer domain and
// a UUID id; anything else renders as recorded text without a link. The
// panel never issues requests and never claims child status.

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

const linkSpy = vi.hoisted(() => vi.fn());
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: ReactNode; href: string } & Record<string, unknown>) => {
    linkSpy(href);
    return <a href={href} {...rest}>{children}</a>;
  },
}));

import { TaskChildObservationPanel } from "../components/tasks/task-child-observation-panel";
import {
  localTaskLinkFromRef,
  type AgentChildrenSummary,
  type TaskDetail,
  type WorkflowParentStamp,
  type WorkflowStepsSummary,
} from "@/lib/task-types";

const CHILD_ID = "22222222-2222-4222-8222-222222222222";
const PARENT_ID = "11111111-1111-4111-8111-111111111111";
const LOCAL_REF = { kind: "task", issuer_domain: "hecate", id: CHILD_ID };

function detailOf(overrides: Partial<TaskDetail> = {}): TaskDetail {
  return {
    task_id: "task-1",
    goal: "goal",
    issuer_domain: "hecate",
    workspace_id: "ws-1",
    lifecycle_state: "succeeded",
    revision: 3,
    created_at: "2026-10-27T00:00:00Z",
    runs: [],
    ...overrides,
  };
}

function childrenOf(overrides: Partial<AgentChildrenSummary> = {}): AgentChildrenSummary {
  return {
    accepted_calls: 1,
    pending: { alias: "worker", call_id: "call-1", child_task_ref: LOCAL_REF, consumed: false },
    ...overrides,
  };
}

beforeEach(() => {
  linkSpy.mockClear();
});

describe("TaskChildObservationPanel", () => {
  it("renders nothing recorded when both summaries are absent, without implying delegation is off", () => {
    render(<TaskChildObservationPanel detail={detailOf()} />);
    const panel = screen.getByTestId("agent-observation-panel");
    expect(within(panel).getByTestId("agent-observation-absent").textContent).toContain("无已记录的父子调用信息");
    expect(within(panel).getByText(/不能据此推断委托能力已关闭/)).toBeInTheDocument();
    expect(screen.queryByTestId("agent-observation-parent-section")).not.toBeInTheDocument();
    expect(screen.queryByTestId("agent-observation-child-section")).not.toBeInTheDocument();
  });

  it("shows accepted calls, the pending wait and a validated child link on the parent view", () => {
    render(<TaskChildObservationPanel detail={detailOf({ agent_children: childrenOf() })} />);
    const view = screen.getByTestId("agent-children-view");
    expect(within(view).getByTestId("agent-children-accepted-calls").textContent).toContain("已接受调用次数：1");
    expect(within(view).getByTestId("agent-children-consumed-state").textContent).toContain("等待回调消费");
    const link = within(view).getByTestId("agent-children-child-link");
    expect(link.getAttribute("href")).toBe(`/ops-center/tasks/${CHILD_ID}`);
    expect(within(view).getByTestId("agent-children-consumed-note").textContent).toContain(
      "子任务可能仍在执行、可能已结束",
    );
  });

  it("separates a consumed callback from child success and parent success", () => {
    render(
      <TaskChildObservationPanel
        detail={detailOf({ agent_children: childrenOf({ pending: { consumed: true, child_task_ref: LOCAL_REF } }) })}
      />,
    );
    const view = screen.getByTestId("agent-children-view");
    expect(within(view).getByTestId("agent-children-consumed-state").textContent).toContain("回调已消费");
    expect(within(view).getByTestId("agent-children-consumed-note").textContent).toContain(
      "不代表子任务成功，也不代表父任务目标通过",
    );
  });

  it("renders an unknown consumption state as unrecorded, not as consumed", () => {
    render(
      <TaskChildObservationPanel
        detail={detailOf({ agent_children: childrenOf({ pending: { consumed: null, child_task_ref: LOCAL_REF } }) })}
      />,
    );
    expect(screen.getByTestId("agent-children-consumed-state").textContent).toContain("消费状态未记录");
    expect(screen.getByTestId("agent-children-consumed-note").textContent).toContain("无法判断回调是否已消费");
    expect(screen.getByTestId("agent-children-consumed-note").textContent).not.toContain("“回调已消费”只是");
  });

  it("keeps a zero accepted-call count as a recorded fact, distinct from absence", () => {
    render(
      <TaskChildObservationPanel detail={detailOf({ agent_children: { accepted_calls: 0 } })} />,
    );
    expect(screen.getByTestId("agent-children-accepted-calls").textContent).toContain("已接受调用次数：0");
    expect(screen.getByTestId("agent-children-no-pending").textContent).toContain(
      "现在没有处于委托等待状态的调用记录",
    );
  });

  it("marks an unrecorded count instead of inventing zero", () => {
    render(
      <TaskChildObservationPanel
        detail={detailOf({ agent_children: { accepted_calls: null, pending: null } })}
      />,
    );
    expect(screen.getByTestId("agent-children-accepted-calls").textContent).toContain("已接受调用次数：未记录");
  });

  it.each([
    ["wrong kind", { kind: "run", issuer_domain: "hecate", id: CHILD_ID }],
    ["foreign issuer", { kind: "task", issuer_domain: "standalone-host", id: CHILD_ID }],
    ["non-uuid id", { kind: "task", issuer_domain: "hecate", id: "not-a-uuid" }],
    ["missing id", { kind: "task", issuer_domain: "hecate", id: null }],
  ])("refuses to link an invalid child ref (%s) and shows the recorded text", (_name, badRef) => {
    render(
      <TaskChildObservationPanel
        detail={detailOf({ agent_children: childrenOf({ pending: { child_task_ref: badRef } }) })}
      />,
    );
    expect(screen.queryByTestId("agent-children-child-link")).not.toBeInTheDocument();
    const unlinked = screen.getByTestId("agent-ref-unlinked");
    expect(unlinked.textContent).toContain("引用无法在本平台定位");
    expect(linkSpy).not.toHaveBeenCalled();
  });

  it("renders the child view with alias, call identity and a validated parent link", () => {
    render(
      <TaskChildObservationPanel
        detail={detailOf({
          agent_child_parent: { alias: "worker", call_id: "call-1", parent_task_ref: { kind: "task", issuer_domain: "hecate", id: PARENT_ID } },
        })}
      />,
    );
    const view = screen.getByTestId("agent-child-parent-view");
    expect(view.textContent).toContain("调用别名：worker");
    expect(view.textContent).toContain("调用身份：call-1");
    const link = within(view).getByTestId("agent-child-parent-link");
    expect(link.getAttribute("href")).toBe(`/ops-center/tasks/${PARENT_ID}`);
    expect(within(view).getByTestId("agent-child-parent-note").textContent).toContain("它不授予权限");
  });

  it("does not treat matching foreign issuer domains as a local task reference", () => {
    const foreignRef = { ...LOCAL_REF, issuer_domain: "standalone-host" };
    expect(localTaskLinkFromRef(foreignRef, "standalone-host")).toBeNull();
    render(
      <TaskChildObservationPanel
        detail={detailOf({
          issuer_domain: "standalone-host",
          agent_children: childrenOf({ pending: { child_task_ref: foreignRef } }),
          agent_child_parent: { parent_task_ref: foreignRef },
        })}
      />,
    );
    expect(screen.queryByTestId("agent-children-child-link")).not.toBeInTheDocument();
    expect(screen.queryByTestId("agent-child-parent-link")).not.toBeInTheDocument();
    expect(linkSpy).not.toHaveBeenCalled();
  });

  it("renders both views independently for nested delegation", () => {
    render(
      <TaskChildObservationPanel
        detail={detailOf({
          agent_children: childrenOf(),
          agent_child_parent: { alias: "outer", call_id: "call-0", parent_task_ref: { kind: "task", issuer_domain: "hecate", id: PARENT_ID } },
        })}
      />,
    );
    expect(screen.getByTestId("agent-observation-parent-section")).toBeInTheDocument();
    expect(screen.getByTestId("agent-observation-child-section")).toBeInTheDocument();
  });

  it("always states the scope: task-level, current-wait only, no child status claimed here", () => {
    render(<TaskChildObservationPanel detail={detailOf({ agent_children: childrenOf() })} />);
    const note = screen.getByTestId("agent-observation-scope-note");
    expect(note.textContent).toContain("不随所选 Run 切换");
    expect(note.textContent).toContain("不承诺完整调用历史");
    expect(note.textContent).toContain("不要从父任务的等待状态推断子任务结果");
  });
});

describe("TaskChildObservationPanel child-execution budgets", () => {
  it("renders both recorded budget lines with their semantics note", () => {
    render(
      <TaskChildObservationPanel
        detail={detailOf({ agent_children: childrenOf(), agent_child_budget_used: 1200, workflow_budget_used: 300 })}
      />,
    );
    expect(screen.getByTestId("child-budget-agent-used").textContent).toContain("Agent 子调用：1200");
    expect(screen.getByTestId("child-budget-workflow-used").textContent).toContain("Workflow Controller 子步骤：300");
    const note = screen.getByTestId("child-budget-note");
    expect(note.textContent).toContain("已记入父任务的累计用量");
    expect(note.textContent).toContain("不是实时余额");
    expect(note.textContent).toContain("不是已核实的账单");
    expect(note.textContent).toContain("与有效 0 不同");
  });

  it("keeps a recorded zero visible instead of reading it as unrecorded", () => {
    render(
      <TaskChildObservationPanel
        detail={detailOf({ agent_children: { accepted_calls: 1 }, agent_child_budget_used: 0 })}
      />,
    );
    expect(screen.getByTestId("child-budget-agent-used").textContent).toContain("Agent 子调用：0");
    expect(screen.getByTestId("child-budget-agent-used").textContent).not.toContain("未记录");
    // The other counter keeps its own missing state.
    expect(screen.getByTestId("child-budget-workflow-used").textContent).toContain(
      "Workflow Controller 子步骤：未记录",
    );
  });

  it("shows 未记录 for both counters on a pre-feature task without relationship facts", () => {
    render(<TaskChildObservationPanel detail={detailOf()} />);
    expect(screen.getByTestId("agent-observation-absent")).toBeInTheDocument();
    expect(screen.getByTestId("child-budget-agent-used").textContent).toContain("Agent 子调用：未记录");
    expect(screen.getByTestId("child-budget-workflow-used").textContent).toContain(
      "Workflow Controller 子步骤：未记录",
    );
  });
});

describe("TaskChildObservationPanel workflow steps", () => {
  const STEP0_CHILD = "33333333-3333-4333-8333-333333333333";

  function stepsOf(overrides: Partial<WorkflowStepsSummary> = {}): WorkflowStepsSummary {
    return {
      steps: [
        { index: 0, goal: "research the stock", mode: "controller", child_task_id: STEP0_CHILD, callback_applied: true },
        { index: 1, goal: "write the summary", mode: "chat", waiting: { consumed: false } },
      ],
      ...overrides,
    };
  }

  function stampOf(overrides: Partial<WorkflowParentStamp> = {}): WorkflowParentStamp {
    return { parent_task_ref: { kind: "task", issuer_domain: "hecate", id: PARENT_ID }, step_index: 0, ...overrides };
  }

  it("renders declared steps in order with mode, goal and a validated child link", () => {
    render(<TaskChildObservationPanel detail={detailOf({ workflow_steps: stepsOf() })} />);
    expect(screen.queryByTestId("agent-observation-absent")).not.toBeInTheDocument();
    expect(screen.getByTestId("workflow-step-0").textContent).toContain("#0");
    expect(screen.getByTestId("workflow-step-0-mode").textContent).toContain("controller");
    expect(screen.getByTestId("workflow-step-0").textContent).toContain("research the stock");
    expect(screen.getByTestId("workflow-step-0-child-link").getAttribute("href")).toBe(
      `/ops-center/tasks/${STEP0_CHILD}`,
    );
    expect(screen.getByTestId("workflow-step-1-mode").textContent).toContain("chat");
    expect(screen.queryByTestId("workflow-step-1-child-link")).not.toBeInTheDocument();
    const note = screen.getByTestId("workflow-steps-scope-note");
    expect(note.textContent).toContain("回调已消费不代表子任务成功");
    expect(note.textContent).toContain("缺失字段表示该事实未记录");
  });

  it("separates the current wait, the consumption fact and verbatim markers", () => {
    render(
      <TaskChildObservationPanel
        detail={detailOf({
          workflow_steps: stepsOf({
            steps: [
              { index: 0, goal: "a", mode: "chat", callback_applied: true, step_state: "held_for_reconciliation" },
              { index: 1, goal: "b", mode: "chat", waiting: { consumed: true } },
              { index: 2, goal: "c", mode: "controller", waiting: { consumed: null } },
              { index: 3, goal: "d", mode: "chat", step_state: "closed_unexecuted" },
            ],
          }),
        })}
      />,
    );
    expect(screen.getByTestId("workflow-step-0-callback").textContent).toContain("回调已消费");
    expect(screen.getByTestId("workflow-step-0-state").textContent).toContain("记录标记：held_for_reconciliation");
    expect(screen.getByTestId("workflow-step-1-waiting").textContent).toContain("当前等待的回调已消费");
    expect(screen.getByTestId("workflow-step-1-waiting").textContent).not.toContain("尚未推进");
    expect(screen.getByTestId("workflow-step-2-waiting").textContent).toContain("消费状态未记录");
    expect(screen.getByTestId("workflow-step-3-state").textContent).toContain("记录标记：closed_unexecuted");
  });

  it("renders unverifiable and contradicted facts without inventing a child link", () => {
    render(
      <TaskChildObservationPanel
        detail={detailOf({
          workflow_steps: stepsOf({
            steps: [
              { index: 0, goal: "a", mode: "chat", child_unverified: true },
              { index: 1, goal: "b", mode: "chat", binding_inconsistent: true },
              { index: 2, goal: "c", mode: "chat", no_submission: true },
            ],
          }),
        })}
      />,
    );
    expect(screen.getByTestId("workflow-step-0-unverified").textContent).toContain("无法在本工作区核验");
    expect(screen.getByTestId("workflow-step-1-inconsistent").textContent).toContain("记录事实不一致");
    expect(screen.getByTestId("workflow-step-2-no-submission").textContent).toContain("未找到提交记录");
    expect(screen.queryByTestId(/workflow-step-\d+-child-link$/)).not.toBeInTheDocument();
    expect(linkSpy).not.toHaveBeenCalled();
  });

  it("states an unreadable declaration and an unsupported submission surface", async () => {
    const first = render(
      <TaskChildObservationPanel
        detail={detailOf({ workflow_steps: stepsOf({ declaration_unreadable: true, steps: [] }) })}
      />,
    );
    expect(screen.getByTestId("workflow-steps-unreadable").textContent).toContain("无法解析");
    expect(screen.queryByTestId("workflow-step-list")).not.toBeInTheDocument();
    first.unmount();

    render(<TaskChildObservationPanel detail={detailOf({ workflow_steps: stepsOf({ submissions_unsupported: true }) })} />);
    expect(screen.getByTestId("workflow-steps-unsupported").textContent).toContain("不支持读取步骤提交记录");
    expect(screen.getByTestId("workflow-step-0").textContent).toContain("#0");
  });

  it("shows a failed submission read without claiming the submission is absent", () => {
    render(
      <TaskChildObservationPanel
        detail={detailOf({
          workflow_steps: stepsOf({ steps: [{ index: 0, goal: "a", mode: "chat", submission_read_failed: true }] }),
        })}
      />,
    );
    expect(screen.getByTestId("workflow-step-0-read-failed").textContent).toContain("读取失败");
    expect(screen.queryByTestId("workflow-step-0-no-submission")).not.toBeInTheDocument();
    expect(screen.queryByTestId("workflow-step-0-child-link")).not.toBeInTheDocument();
  });

  it.each(["parent_unverified", "binding_inconsistent"] as const)(
    "withholds navigation for a %s parent relationship",
    (flag) => {
      render(<TaskChildObservationPanel detail={detailOf({ workflow_parent: stampOf({ [flag]: true }) })} />);
      expect(screen.queryByTestId("workflow-child-parent-link")).not.toBeInTheDocument();
      expect(screen.queryByTestId("workflow-child-parent-ref")).not.toBeInTheDocument();
      expect(linkSpy).not.toHaveBeenCalled();
    },
  );

  it("renders the workflow child-side stamp with the back-to-parent navigation", () => {
    render(<TaskChildObservationPanel detail={detailOf({ workflow_parent: stampOf() })} />);
    expect(screen.getByTestId("workflow-child-parent-step-index").textContent).toContain("步骤序号：0");
    expect(screen.getByTestId("workflow-child-parent-link").getAttribute("href")).toBe(
      `/ops-center/tasks/${PARENT_ID}`,
    );
    expect(screen.getByTestId("workflow-child-parent-note").textContent).toContain("它不授予权限");
  });

  it("falls back to recorded text for an invalid parent stamp ref", () => {
    render(
      <TaskChildObservationPanel
        detail={detailOf({ workflow_parent: stampOf({ parent_task_ref: { kind: "task", issuer_domain: "hecate", id: "not-a-uuid" } }) })}
      />,
    );
    expect(screen.queryByTestId("workflow-child-parent-link")).not.toBeInTheDocument();
    expect(screen.getByTestId("agent-ref-unlinked")).toBeInTheDocument();
    expect(linkSpy).not.toHaveBeenCalled();
  });

  it("renders a recorded 未记录 step index instead of inventing one", () => {
    render(<TaskChildObservationPanel detail={detailOf({ workflow_parent: { parent_task_ref: LOCAL_REF } })} />);
    expect(screen.getByTestId("workflow-child-parent-step-index").textContent).toContain("步骤序号：未记录");
  });
});
