"use client";

import Link from "next/link";

import {
  localTaskLinkFromRef,
  type AgentChildParentStamp,
  type AgentChildrenSummary,
  type AgentTaskRef,
  type TaskDetail,
} from "@/lib/task-types";

/**
 * Task-level parent-child delegation observation, read from the task
 * detail's read-only summary. Everything shown is a recorded fact; the
 * panel issues no request of its own and offers no action: it never
 * submits input, resume, cancel or callback commands, and a relationship
 * reference never grants access — child/parent details stay behind their
 * own authorized GET and workspace checks.
 *
 * Four facts stay separate everywhere: a call was accepted (count /
 * stamp), the current wait's callback was consumed, the child task's
 * lifecycle (readable ONLY through the child task's own detail — never
 * inferred here), and the delivery/acceptance verdict (each task's own
 * panels). `consumed=true` does not mean the child succeeded and does not
 * mean the parent's goal passed. The API summarizes only the CURRENT
 * wait; the panel claims no call history.
 */

const ABSENT_HINT =
  "任务详情未返回父子调用摘要：该任务可能不属于任何委托调用，或执行早于该功能。不能据此推断委托能力已关闭或已禁用。";

const NO_PENDING_HINT =
  "摘要未包含当前等待：现在没有处于委托等待状态的调用记录。这不代表调用历史为空，也不代表此前的等待从未发生。";

const CONSUMED_NOTE =
  "“回调已消费”只是该等待的消费事实：不代表子任务成功，也不代表父任务目标通过。子任务的生命周期与交付验收请通过子任务详情查看。";

const UNCONSUMED_NOTE =
  "“等待回调消费”表示该调用已被接受且其回调尚未消费：子任务可能仍在执行、可能已结束，父任务详情不携带子任务状态，请通过子任务详情查看。";

const UNKNOWN_CONSUMPTION_NOTE =
  "消费状态未记录：无法判断回调是否已消费，也不能据此判断子任务状态或父任务目标是否通过。";

const SCOPE_NOTE =
  "父子调用摘要是任务级记录，不随所选 Run 切换。当前 API 只提供调用计数与当前等待摘要，不承诺完整调用历史；本面板不显示子任务状态，也不要从父任务的等待状态推断子任务结果。";

const REF_INVALID_NOTE = "引用无法在本平台定位：未生成本地链接，仅按记录原样展示。";

function RefText({ taskRef }: { taskRef: AgentTaskRef | null }) {
  const recorded = taskRef ? JSON.stringify(taskRef) : null;
  return (
    <div className="space-y-1" data-testid="agent-ref-unlinked">
      <span className="font-mono text-xs break-all" title={recorded ?? undefined}>
        {recorded ?? "未记录"}
      </span>
      <p className="text-xs text-muted-foreground">{REF_INVALID_NOTE}</p>
    </div>
  );
}

function RefLink({
  taskRef,
  issuerDomain,
  label,
  testId,
}: {
  taskRef: AgentTaskRef | null;
  issuerDomain: string;
  label: string;
  testId: string;
}) {
  const href = localTaskLinkFromRef(taskRef, issuerDomain);
  if (!href) return <RefText taskRef={taskRef} />;
  return (
    <Link href={href} className="text-sm hover:underline" data-testid={testId}>
      {label}
    </Link>
  );
}

function ParentDelegationView({ summary, issuerDomain }: { summary: AgentChildrenSummary; issuerDomain: string }) {
  const accepted = typeof summary.accepted_calls === "number" && Number.isSafeInteger(summary.accepted_calls)
    ? String(summary.accepted_calls)
    : null;
  const pending = summary.pending ?? null;
  const consumed = pending ? pending.consumed : null;
  return (
    <div className="space-y-3" data-testid="agent-children-view">
      <p className="text-sm" data-testid="agent-children-accepted-calls">
        已接受调用次数：{accepted ?? "未记录"}
      </p>

      {pending ? (
        <div className="space-y-2 rounded border p-3" data-testid="agent-children-pending">
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
            <span className="text-muted-foreground">调用别名：{pending.alias ?? "未记录"}</span>
            <span className="text-muted-foreground">调用身份：{pending.call_id ?? "未记录"}</span>
          </div>
          <div className="text-sm" data-testid="agent-children-consumed-state">
            {consumed === true ? "回调已消费" : consumed === false ? "等待回调消费" : "消费状态未记录"}
          </div>
          <div data-testid="agent-children-child-ref">
            <RefLink
              taskRef={pending.child_task_ref ?? null}
              issuerDomain={issuerDomain}
              label="查看子任务详情 →"
              testId="agent-children-child-link"
            />
          </div>
          <p className="text-xs text-muted-foreground" data-testid="agent-children-consumed-note">
            {consumed === true ? CONSUMED_NOTE : consumed === false ? UNCONSUMED_NOTE : UNKNOWN_CONSUMPTION_NOTE}
          </p>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground" data-testid="agent-children-no-pending">
          {NO_PENDING_HINT}
        </p>
      )}
    </div>
  );
}

function ChildDelegationView({ stamp, issuerDomain }: { stamp: AgentChildParentStamp; issuerDomain: string }) {
  return (
    <div className="space-y-3" data-testid="agent-child-parent-view">
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <span className="text-muted-foreground">调用别名：{stamp.alias ?? "未记录"}</span>
        <span className="text-muted-foreground">调用身份：{stamp.call_id ?? "未记录"}</span>
      </div>
      <div data-testid="agent-child-parent-ref">
        <RefLink
          taskRef={stamp.parent_task_ref ?? null}
          issuerDomain={issuerDomain}
          label="← 返回父任务详情"
          testId="agent-child-parent-link"
        />
      </div>
      <p className="text-xs text-muted-foreground" data-testid="agent-child-parent-note">
        父任务引用只标识委托来源：它不授予权限，也不代表父任务的目标已通过。本任务自身的生命周期与交付验收由本页其它面板呈现。
      </p>
    </div>
  );
}

export function TaskChildObservationPanel({ detail }: { detail: TaskDetail }) {
  const parentSummary = detail.agent_children;
  const childStamp = detail.agent_child_parent;
  if (!parentSummary && !childStamp) {
    return (
      <div className="space-y-1" data-testid="agent-observation-panel" aria-live="polite">
        <p className="text-sm text-muted-foreground" data-testid="agent-observation-absent">
          无已记录的父子调用信息
        </p>
        <p className="text-xs text-muted-foreground">{ABSENT_HINT}</p>
      </div>
    );
  }
  return (
    <div className="space-y-3" data-testid="agent-observation-panel" aria-live="polite">
      {parentSummary && (
        <div className="space-y-1" data-testid="agent-observation-parent-section">
          <p className="text-sm font-medium">父任务视角（委派调用）</p>
          <ParentDelegationView summary={parentSummary} issuerDomain={detail.issuer_domain} />
        </div>
      )}
      {childStamp && (
        <div className="space-y-1" data-testid="agent-observation-child-section">
          <p className="text-sm font-medium">子任务视角（被委派）</p>
          <ChildDelegationView stamp={childStamp} issuerDomain={detail.issuer_domain} />
        </div>
      )}
      <p className="text-xs text-muted-foreground" data-testid="agent-observation-scope-note">
        {SCOPE_NOTE}
      </p>
    </div>
  );
}
