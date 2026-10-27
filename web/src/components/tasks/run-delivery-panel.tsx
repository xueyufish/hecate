"use client";

import { AcceptanceBadge, LifecycleBadge } from "@/components/tasks/state-badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  acceptanceLabel,
  checkStatusLabel,
  digestPreview,
  type RunSummary,
} from "@/lib/task-types";

/**
 * Run-level delivery and acceptance facts, read from the task detail's
 * already-authorized per-run summary. Everything shown is a recorded fact:
 * the panel never re-evaluates a check, never recomputes a digest from the
 * preview, and never presents the task's latest result as a historical
 * run's verdict.
 */

const NO_RECORD_HINT =
  "该 Run 没有已记录的验收评估：可能是执行早于验收记录功能，或该次执行未经过交付验收评估。";

function asString(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
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

function EvaluatedBy({ evaluatedBy }: { evaluatedBy: Record<string, unknown> }) {
  const host = asString(evaluatedBy.host);
  const submittedBy = asString(evaluatedBy.submittedBy) ?? asString(evaluatedBy.submitted_by);
  const principal = asString(evaluatedBy.principal);
  const who = submittedBy ?? principal;
  if (!host && !who) return null;
  return (
    <span className="text-muted-foreground">
      责任主体：{host ? `${host}` : "未记录"}
      {who ? `（${who}）` : ""}
    </span>
  );
}

export function RunDeliveryPanel({ run }: { run: RunSummary }) {
  const projection = run.projection ?? {};
  const state = asString(projection.state);
  const preview = asString(projection.result_preview);
  const truncated = projection.result_preview_truncated === true;
  const acceptance = run.delivery_acceptance;
  const error = asString(projection.error);
  const errorCategory = asString(projection.error_category);

  return (
    <div className="space-y-3" data-testid="delivery-panel" aria-live="polite">
      <div className="flex flex-wrap items-center gap-3" data-testid="delivery-execution">
        <span className="text-sm text-muted-foreground">执行状态：</span>
        <LifecycleBadge state={state} />
        {errorCategory && (
          <span className="text-xs text-muted-foreground" data-testid="delivery-error-category">
            停止类别：{errorCategory}
          </span>
        )}
      </div>

      <div className="space-y-1" data-testid="delivery-preview">
        <p className="text-sm font-medium">交付内容</p>
        {preview ? (
          <>
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded border bg-muted/40 p-2 text-xs font-mono">
              {preview}
            </pre>
            {truncated && (
              <p className="text-xs text-muted-foreground" data-testid="delivery-preview-truncated">
                预览已截断（仅前 2000 字符）。完整性以记录的内容摘要为准，不用预览重新验证。
              </p>
            )}
          </>
        ) : (
          <p className="text-sm text-muted-foreground" data-testid="delivery-preview-empty">
            无已记录的交付内容预览。
          </p>
        )}
        {error && (
          <p className="whitespace-pre-wrap break-words text-xs text-destructive" data-testid="delivery-error">
            {error}
          </p>
        )}
      </div>

      <div className="space-y-2" data-testid="delivery-acceptance">
        <p className="text-sm font-medium">交付验收</p>
        {!acceptance ? (
          <div className="space-y-1">
            <p className="text-sm text-muted-foreground" data-testid="delivery-no-record">
              无验收记录
            </p>
            <p className="text-xs text-muted-foreground">{NO_RECORD_HINT}</p>
          </div>
        ) : (
          <div className="space-y-2" data-testid="delivery-acceptance-record">
            <div className="flex flex-wrap items-center gap-3">
              <AcceptanceBadge status={acceptance.status} />
              {acceptance.status === "not_declared" && (
                <span className="text-xs text-muted-foreground">
                  任务成功仅表示有交付，未声明验收标准，不构成业务目标验收。
                </span>
              )}
              {acceptance.status === "uninterpretable" && (
                <span className="text-xs text-destructive" data-testid="delivery-uninterpretable-reason">
                  {acceptance.reason ?? "记录损坏或使用未知 checker 版本，不能视为通过。"}
                  {acceptance.recorded_status ? `（原记录状态：${acceptanceLabel(acceptance.recorded_status)}）` : ""}
                </span>
              )}
            </div>

            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs" data-testid="delivery-acceptance-meta">
              <span className="text-muted-foreground">
                标准版本：{acceptance.version != null ? String(acceptance.version) : "—"}
              </span>
              <DigestCell label="标准摘要：" digest={acceptance.declaration_sha256 ?? null} />
              <DigestCell label="内容摘要：" digest={acceptance.content_sha256 ?? null} />
              <DigestCell label="记录摘要：" digest={acceptance.record_sha256 ?? null} />
              <span className="text-muted-foreground">
                checker 版本：{acceptance.checker_version ?? "未记录"}
              </span>
            </div>

            {(acceptance.final_decision?.decision_step_id ||
              acceptance.final_decision?.content_digest) && (
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs" data-testid="delivery-final-decision">
                <span className="text-muted-foreground">
                  最终决策：{acceptance.final_decision?.decision_step_id ?? "未记录"}
                </span>
                <DigestCell
                  label="决策摘要："
                  digest={acceptance.final_decision?.content_digest ?? null}
                />
              </div>
            )}

            {acceptance.evaluated_by && (
              <div className="text-xs" data-testid="delivery-evaluated-by">
                <EvaluatedBy evaluatedBy={acceptance.evaluated_by} />
              </div>
            )}

            {acceptance.checks && acceptance.checks.length > 0 ? (
              <Table data-testid="delivery-checks">
                <TableHeader>
                  <TableRow>
                    <TableHead>检查</TableHead>
                    <TableHead>结果</TableHead>
                    <TableHead>说明</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {acceptance.checks.map((check, index) => (
                    <TableRow key={`${check.kind ?? "check"}-${index}`} data-testid="delivery-check-row">
                      <TableCell className="font-mono text-xs">{check.kind ?? "未记录"}</TableCell>
                      <TableCell>{checkStatusLabel(check.status)}</TableCell>
                      <TableCell className="whitespace-pre-wrap break-words text-xs">
                        {check.detail ?? "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              acceptance.status === "not_declared" && (
                <p className="text-xs text-muted-foreground">无必需检查（未声明验收标准）。</p>
              )
            )}

            <p className="text-xs text-muted-foreground" data-testid="delivery-acceptance-note">
              验收结论只表示已记录的检查在记录时的判定，不等于业务目标全面达成。
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
