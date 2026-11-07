"use client";

import Link from "next/link";

import {
  localTaskLinkFromRef,
  type AgentChildParentStamp,
  type AgentChildrenSummary,
  type AgentTaskRef,
  type TaskDetail,
  type WorkflowParentStamp,
  type WorkflowStepObservation,
  type WorkflowStepsSummary,
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
 *
 * The budget lines are the parent's persisted cumulative child-execution
 * usage (agent-child calls and workflow controller steps), already folded
 * in by consumed/verified callbacks. A recorded `0` is real and is shown
 * as such; 未记录 means the counter was never recorded. They are not a
 * real-time balance, not the task's full usage, and not verified billing.
 *
 * The workflow step list is the same kind of task-level recorded fact:
 * declared steps (index, goal, mode) joined with the parent's persisted
 * submission/callback/marker facts. A child link renders only for a
 * submitted child that re-verified inside this workspace; consumption
 * facts never imply the child succeeded, and the child's lifecycle and
 * delivery verdict stay on the child task's own detail page.
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

const BUDGET_HEADING = "子执行累计预算（已记入父任务）";

const BUDGET_SCOPE_NOTE =
  "两个数字分别是 Agent 子调用与 Workflow Controller 子步骤已记入父任务的累计用量：只在对应的子执行回调被消费后累加并持久化。它们不是实时余额，不覆盖本任务全部用量，也不是已核实的账单；“未记录”表示该计数从未记入，与有效 0 不同。";

const WORKFLOW_SCOPE_NOTE =
  "步骤列表按声明顺序展示任务级记录事实：提交记录、当前等待、回调消费与持久化标记（按原记录原样显示）。回调已消费不代表子任务成功，也不代表父任务目标通过；子任务的生命周期与交付验收请通过子任务详情查看。缺失字段表示该事实未记录，不是否定。";

const WORKFLOW_UNREADABLE_NOTE =
  "持久化的 Workflow 声明无法解析：步骤列表不可用。不能据此推断步骤从未存在，也不能推断声明已被修改。";

const WORKFLOW_UNSUPPORTED_NOTE =
  "当前存储不支持读取步骤提交记录：步骤仅展示声明与已记录的消费/标记事实，无法定位已提交的子任务。";

const WORKFLOW_WAITING_NOTE = "当前等待回调消费：该步骤已提交且其回调尚未消费。";

const WORKFLOW_WAITING_CONSUMED_NOTE = "当前等待的回调已消费。";

const WORKFLOW_WAITING_UNKNOWN_NOTE = "当前等待绑定该步骤：消费状态未记录。";

function recordedCount(value: number | undefined): string {
  return typeof value === "number" && Number.isSafeInteger(value) ? String(value) : "未记录";
}

/** Both budget lines always render so a pre-feature task reads 未记录
 * instead of silently looking like zero. Each counter handles its own
 * missing state; neither line is ever derived from the other. */
function ChildBudgetView({ detail }: { detail: TaskDetail }) {
  return (
    <div className="space-y-1" data-testid="child-budget-view">
      <p className="text-sm font-medium">{BUDGET_HEADING}</p>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
        <span data-testid="child-budget-agent-used">
          Agent 子调用：{recordedCount(detail.agent_child_budget_used)}
        </span>
        <span data-testid="child-budget-workflow-used">
          Workflow Controller 子步骤：{recordedCount(detail.workflow_budget_used)}
        </span>
      </div>
      <p className="text-xs text-muted-foreground" data-testid="child-budget-note">
        {BUDGET_SCOPE_NOTE}
      </p>
    </div>
  );
}

/** Both lines of the workflow section render from recorded facts only:
 * a child link exists iff the submitted child re-verified here, and the
 * persisted markers are echoed verbatim instead of being paraphrased. */
function WorkflowStepFacts({ step, issuerDomain }: { step: WorkflowStepObservation; issuerDomain: string }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs" data-testid={`workflow-step-${step.index}-facts`}>
      {step.child_task_id && (
        <RefLink
          taskRef={{ kind: "task", issuer_domain: issuerDomain, id: step.child_task_id }}
          issuerDomain={issuerDomain}
          label="查看子任务详情 →"
          testId={`workflow-step-${step.index}-child-link`}
        />
      )}
      {step.waiting && (
        <span data-testid={`workflow-step-${step.index}-waiting`}>
          {step.waiting.consumed === true
            ? WORKFLOW_WAITING_CONSUMED_NOTE
            : step.waiting.consumed === false
              ? WORKFLOW_WAITING_NOTE
              : WORKFLOW_WAITING_UNKNOWN_NOTE}
        </span>
      )}
      {step.callback_applied && <span data-testid={`workflow-step-${step.index}-callback`}>回调已消费</span>}
      {step.step_state && (
        <span className="font-mono" data-testid={`workflow-step-${step.index}-state`}>
          记录标记：{step.step_state}
        </span>
      )}
      {step.child_unverified && (
        <span data-testid={`workflow-step-${step.index}-unverified`}>
          提交记录无法在本工作区核验：已隐藏子任务引用
        </span>
      )}
      {step.submission_read_failed && (
        <span data-testid={`workflow-step-${step.index}-read-failed`}>
          步骤提交记录读取失败，请刷新重试
        </span>
      )}
      {step.binding_inconsistent && (
        <span data-testid={`workflow-step-${step.index}-inconsistent`}>记录事实不一致：持久化事实指向的子任务不一致</span>
      )}
      {step.no_submission && <span data-testid={`workflow-step-${step.index}-no-submission`}>未找到提交记录</span>}
    </div>
  );
}

function WorkflowStepsView({ summary, issuerDomain }: { summary: WorkflowStepsSummary; issuerDomain: string }) {
  return (
    <div className="space-y-3" data-testid="workflow-steps-view">
      {summary.declaration_unreadable && (
        <p className="text-xs text-muted-foreground" data-testid="workflow-steps-unreadable">
          {WORKFLOW_UNREADABLE_NOTE}
        </p>
      )}
      {summary.submissions_unsupported && (
        <p className="text-xs text-muted-foreground" data-testid="workflow-steps-unsupported">
          {WORKFLOW_UNSUPPORTED_NOTE}
        </p>
      )}
      {!summary.declaration_unreadable && (
        <ul className="space-y-2" data-testid="workflow-step-list">
          {summary.steps.map((step) => (
            <li key={step.index} className="space-y-1 rounded border p-3" data-testid={`workflow-step-${step.index}`}>
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm">
                <span className="font-medium" data-testid={`workflow-step-${step.index}-index`}>
                  #{step.index}
                </span>
                <span className="text-muted-foreground" data-testid={`workflow-step-${step.index}-mode`}>
                  {step.mode}
                </span>
                <span className="break-all">{step.goal}</span>
              </div>
              <WorkflowStepFacts step={step} issuerDomain={issuerDomain} />
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground" data-testid="workflow-steps-scope-note">
        {WORKFLOW_SCOPE_NOTE}
      </p>
    </div>
  );
}

function WorkflowChildParentView({ stamp, issuerDomain }: { stamp: WorkflowParentStamp; issuerDomain: string }) {
  return (
    <div className="space-y-3" data-testid="workflow-child-parent-view">
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <span className="text-muted-foreground" data-testid="workflow-child-parent-step-index">
          步骤序号：{typeof stamp.step_index === "number" ? stamp.step_index : "未记录"}
        </span>
      </div>
      {stamp.parent_unverified && (
        <p className="text-xs text-muted-foreground" data-testid="workflow-child-parent-unverified">
          父任务无法在本工作区核验：已隐藏父任务引用
        </p>
      )}
      {stamp.binding_inconsistent && (
        <p className="text-xs text-muted-foreground" data-testid="workflow-child-parent-inconsistent">
          父子任务绑定不一致：已隐藏父任务引用
        </p>
      )}
      {stamp.parent_task_ref && !stamp.parent_unverified && !stamp.binding_inconsistent && (
        <div data-testid="workflow-child-parent-ref">
          <RefLink
            taskRef={stamp.parent_task_ref}
            issuerDomain={issuerDomain}
            label="← 返回父任务详情"
            testId="workflow-child-parent-link"
          />
        </div>
      )}
      <p className="text-xs text-muted-foreground" data-testid="workflow-child-parent-note">
        父任务引用只标识本步骤的提交来源：它不授予权限，也不代表父任务目标已通过。本任务自身的生命周期与交付验收由本页其它面板呈现。
      </p>
    </div>
  );
}

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
  const workflowSteps = detail.workflow_steps;
  const workflowStamp = detail.workflow_parent;
  if (!parentSummary && !childStamp && !workflowSteps && !workflowStamp) {
    return (
      <div className="space-y-1" data-testid="agent-observation-panel" aria-live="polite">
        <p className="text-sm text-muted-foreground" data-testid="agent-observation-absent">
          无已记录的父子调用信息
        </p>
        <p className="text-xs text-muted-foreground">{ABSENT_HINT}</p>
        <ChildBudgetView detail={detail} />
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
      {workflowSteps && (
        <div className="space-y-1" data-testid="agent-observation-workflow-section">
          <p className="text-sm font-medium">Workflow 步骤（任务级）</p>
          <WorkflowStepsView summary={workflowSteps} issuerDomain={detail.issuer_domain} />
        </div>
      )}
      {workflowStamp && (
        <div className="space-y-1" data-testid="agent-observation-workflow-child-section">
          <p className="text-sm font-medium">Workflow 子任务视角</p>
          <WorkflowChildParentView stamp={workflowStamp} issuerDomain={detail.issuer_domain} />
        </div>
      )}
      <ChildBudgetView detail={detail} />
      <p className="text-xs text-muted-foreground" data-testid="agent-observation-scope-note">
        {SCOPE_NOTE}
      </p>
    </div>
  );
}
