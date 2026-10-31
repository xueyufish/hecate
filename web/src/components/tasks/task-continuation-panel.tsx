"use client";

import { AcceptanceBadge } from "@/components/tasks/state-badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  checkStatusLabel,
  digestPreview,
  type ContinuationAttemptSummary,
  type TaskContinuationSummary,
} from "@/lib/task-types";

/**
 * Task-level controlled-continuation trail, read from the task detail's
 * read-only continuation summary. Everything shown is a recorded fact: the
 * panel never evaluates a check, never issues a continuation or resume
 * command, and never presents the trail as the selected run's history — the
 * run's final verdict stays in the per-run delivery/acceptance panel.
 *
 * Absence of the summary is displayed as "no recorded continuation
 * information" and never as the policy being off; a recorded trigger never
 * claims the follow-up model call completed; the triggered count comes from
 * the recorded entries and never claims total turns, cost or remaining
 * budget.
 */

const ABSENT_HINT =
  "任务详情未返回续跑摘要：该任务可能没有已记录的续跑信息，或执行早于该功能。不能据此推断续跑策略已关闭。";

const BOUND_REACHED_HINT =
  "已记录触发次数达到续跑上限：不再触发新的受控续跑。这不表示任务失败；任务状态见页面顶部，所选 Run 的最终验收结论另行展示。";

function asInt(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0 ? value : null;
}

function DigestCell({ label, digest }: { label: string; digest: string | null }) {
  if (!digest) return null;
  return (
    <div className="flex gap-1">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono text-xs" title={digest}>
        {digestPreview(digest)}
      </span>
    </div>
  );
}

function AttemptItem({ item }: { item: ContinuationAttemptSummary }) {
  const attemptNo = asInt(item.attempt);
  const standardVersion = asInt(item.standard_version);
  const effectiveTurn = asInt(item.effective_turn);
  const checks = Array.isArray(item.failed_checks) ? item.failed_checks : [];
  return (
    <div className="space-y-2 rounded border p-3" data-testid="continuation-attempt-row">
      <div className="flex flex-wrap items-center gap-3" data-testid="continuation-attempt-header">
        <span className="text-sm font-medium" data-testid="continuation-attempt-index">
          触发序号：{attemptNo != null ? String(attemptNo) : "未记录"}
        </span>
        <AcceptanceBadge status={item.status} />
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs" data-testid="continuation-attempt-meta">
        <span className="text-muted-foreground">
          候选决策：{item.candidate_decision_step_id ?? "未记录"}
        </span>
        <DigestCell label="候选摘要：" digest={item.candidate_digest ?? null} />
        <span className="text-muted-foreground">
          标准版本：{standardVersion != null ? String(standardVersion) : "—"}
        </span>
        <span className="text-muted-foreground">
          生效回合：{effectiveTurn != null ? String(effectiveTurn) : "—"}
        </span>
      </div>

      {checks.length > 0 && (
        <Table data-testid="continuation-failed-checks">
          <TableHeader>
            <TableRow>
              <TableHead>失败检查</TableHead>
              <TableHead>结果</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {checks.map((check, index) => (
              <TableRow key={`${check.kind ?? "check"}-${index}`} data-testid="continuation-check-row">
                <TableCell className="font-mono text-xs">{check.kind ?? "未记录"}</TableCell>
                <TableCell>{checkStatusLabel(check.status)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

export function TaskContinuationPanel({ continuation }: { continuation?: TaskContinuationSummary | null }) {
  if (!continuation) {
    return (
      <div className="space-y-1" data-testid="continuation-panel" aria-live="polite">
        <p className="text-sm text-muted-foreground" data-testid="continuation-absent">
          无已记录的续跑信息
        </p>
        <p className="text-xs text-muted-foreground">{ABSENT_HINT}</p>
      </div>
    );
  }

  const attempts = Array.isArray(continuation.attempts) ? continuation.attempts : null;
  const bound = asInt(continuation.max_continuations);
  const boundReached = bound != null && attempts != null && attempts.length >= bound;

  return (
    <div className="space-y-3" data-testid="continuation-panel" aria-live="polite">
      <div className="flex flex-wrap items-center gap-3 text-sm" data-testid="continuation-overview">
        <span data-testid="continuation-bound">
          续跑上限：{bound != null ? String(bound) : "未记录"}
        </span>
        <span data-testid="continuation-triggered-count">
          已记录触发次数：{attempts != null ? String(attempts.length) : "未记录"}
        </span>
        {boundReached && (
          <span className="text-xs text-muted-foreground" data-testid="continuation-bound-reached">
            {BOUND_REACHED_HINT}
          </span>
        )}
      </div>

      {attempts == null ? (
        <p className="text-sm text-muted-foreground" data-testid="continuation-attempts-unavailable">
          摘要未提供可读取的触发列表，无法确定已记录触发次数。
        </p>
      ) : attempts.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="continuation-empty">
          摘要已返回，但无已记录的续跑触发。
        </p>
      ) : (
        <div className="space-y-3" data-testid="continuation-attempts">
          {attempts.map((item, index) => (
            <AttemptItem key={asInt(item.attempt) ?? index} item={item} />
          ))}
        </div>
      )}

      <div className="space-y-1 text-xs text-muted-foreground">
        <p data-testid="continuation-scope-note">
          续跑历史为任务级记录，不随所选 Run 切换。历史候选失败是触发当时的验收判定；所选 Run
          的最终验收结论见“交付与验收”面板，两者分别呈现。
        </p>
        <p data-testid="continuation-count-note">
          已记录触发次数来自持久化触发条目，不代表总回合数、成本或剩余预算；触发记录不能证明后续模型调用已完成。
        </p>
      </div>
    </div>
  );
}
