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
import { localTaskLinkFromRef, type AgentChildrenSummary, type TaskDetail } from "@/lib/task-types";

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
