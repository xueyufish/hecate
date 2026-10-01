/**
 * Deterministic in-memory run store. The run lifecycle is fully determined by
 * the request content plus explicit checkpoint advances (vendor-namespace
 * route) - no wall-clock scheduling - so both verification sides observe the
 * same event sequence for the same input.
 *
 * Event-sequence conventions follow the standard samples: a lost event range
 * is declared by a gap envelope whose own source_sequence sits AFTER the hole
 * (sample: hole 5..6, marker at 7, following event at 8).
 */
import { contentFingerprint } from "./canonical.js";
import { callbackToken } from "./tool-gateway.js";
import { validateEchoOutput } from "./validation.js";

export interface Ref {
  kind: string;
  issuer_domain: string;
  id: string;
}

export interface Envelope {
  contract_version: string;
  kind: "event" | "gap";
  event_id: string;
  task_ref: Ref;
  run_ref: Ref;
  source_sequence: number;
  occurred_at: string;
  received_at: string;
  payload_schema_ref?: string;
  payload?: Record<string, unknown>;
  gap?: { from_sequence: number; to_sequence: number };
}

export type RunStateValue = "pending" | "running" | "succeeded" | "failed" | "cancelled" | "unknown";

export interface RunRecord {
  callerScope: string;
  runRef: Ref;
  taskRef: Ref;
  state: RunStateValue;
  events: Envelope[];
  nextSequence: number;
  cancelRequested: boolean;
  idempotencyKey: string;
  receivedAt: string;
}

export const CONTRACT_VERSION = "0.1";
export const SUPPORTED_CONTRACT_VERSIONS = new Set(["0.1"]);
const PAYLOAD_BASE = "https://hecate.dev/contracts/execution/0.1/event-payloads";

export class IdempotencyConflictError extends Error {
  readonly originalRunRef: Ref;
  constructor(originalRunRef: Ref) {
    super("same key replayed with different request content");
    this.originalRunRef = originalRunRef;
  }
}

export class UnknownRunError extends Error {
  constructor() {
    super("run reference is not known to this backend");
  }
}

export interface SubmitOutcome {
  created: boolean;
  receipt: { run_ref: Ref; received_at: string };
  record: RunRecord;
}

export class RunStore {
  private readonly runs = new Map<string, RunRecord>();
  private readonly idempotency = new Map<string, { fingerprint: string; runKey: string }>();
  private counter = 9000;

  submit(request: Record<string, unknown>, idempotencyKey: string, callerScope: string): SubmitOutcome {
    const fingerprint = contentFingerprint(request);
    const scopedKey = JSON.stringify([callerScope, idempotencyKey]);
    const existing = this.idempotency.get(scopedKey);
    if (existing) {
      if (existing.fingerprint !== fingerprint) {
        const prior = this.runs.get(existing.runKey);
        if (!prior) {
          throw new UnknownRunError();
        }
        throw new IdempotencyConflictError(prior.runRef);
      }
      const record = this.runs.get(existing.runKey);
      if (!record) {
        throw new UnknownRunError();
      }
      return { created: false, receipt: { run_ref: record.runRef, received_at: record.receivedAt }, record };
    }

    this.counter += 1;
    const runRef: Ref = { kind: "run", issuer_domain: "pilot-ts", id: `br-${this.counter}` };
    const taskRef = request.task_ref as Ref;
    const now = new Date().toISOString();
    const record: RunRecord = {
      callerScope,
      runRef,
      taskRef,
      state: "running",
      events: [],
      nextSequence: 0,
      cancelRequested: false,
      idempotencyKey,
      receivedAt: now,
    };
    this.runs.set(`${runRef.issuer_domain}/${runRef.id}`, record);
    this.idempotency.set(scopedKey, { fingerprint, runKey: `${runRef.issuer_domain}/${runRef.id}` });
    return { created: true, receipt: { run_ref: runRef, received_at: now }, record };
  }

  private emit(record: RunRecord, envelope: Omit<Envelope, "contract_version" | "task_ref" | "run_ref" | "source_sequence" | "occurred_at" | "received_at">): void {
    const now = new Date().toISOString();
    record.events.push({
      contract_version: CONTRACT_VERSION,
      task_ref: record.taskRef,
      run_ref: record.runRef,
      occurred_at: now,
      received_at: now,
      ...envelope,
      source_sequence: record.nextSequence,
    });
    record.nextSequence += 1;
  }

  async executeTool(record: RunRecord, request: Record<string, unknown>, callbackUrl: string, tenant: string): Promise<void> {
    const config = (request.backend_config_ns ?? {}) as Record<string, unknown>;
    const pilot = (config.pilot ?? {}) as Record<string, unknown>;
    const tool = (config.tool ?? "echo") as string;
    const parameters = (config.parameters ?? {}) as Record<string, unknown>;
    const objective = (request.input as Record<string, unknown>)["objective"];

    this.emit(record, {
      kind: "event",
      event_id: "evt-started",
      payload_schema_ref: `${PAYLOAD_BASE}/run-started.json`,
      payload: { objective: objective ?? null },
    });
    this.emit(record, {
      kind: "event",
      event_id: "evt-tool-call",
      payload_schema_ref: `${PAYLOAD_BASE}/tool-call.json`,
      payload: { tool, input: parameters },
    });

    let result: Record<string, unknown>;
    try {
      const response = await fetch(callbackUrl, {
        method: "POST",
        redirect: "error",
        signal: AbortSignal.timeout(5000),
        headers: { "content-type": "application/json", authorization: `Bearer ${callbackToken(tenant)}` },
        body: JSON.stringify({
          tool, input: parameters, task_ref: request.task_ref,
          platform_run_ref: request.run_ref, backend_run_ref: record.runRef,
          trace_correlation: request.trace_correlation,
        }),
      });
      if (!response.ok) throw new Error("tool receiver rejected callback");
      result = await response.json() as Record<string, unknown>;
      if (!validateEchoOutput(result)) {
        throw new Error("tool receiver returned an invalid result");
      }
    } catch {
      // No hidden retry; even this readonly callback can have an unknown outcome.
      result = {
        status: "outcome_unknown", reason: "callback outcome unavailable",
        reconciliation: { strategy: "manual_readonly_check", backend_run_ref: record.runRef },
      };
      record.state = "unknown";
    }

    if (pilot["simulate_gap"] === true) {
      // The tool_result event (the next sequence) is lost; the gap marker
      // occupies the slot after the hole and declares the missing range.
      const lostFrom = record.nextSequence;
      record.nextSequence += 1;
      this.emit(record, {
        kind: "gap",
        event_id: "gap-1",
        gap: { from_sequence: lostFrom, to_sequence: lostFrom },
      });
    } else {
      this.emit(record, {
        kind: "event",
        event_id: "evt-tool-result",
        payload_schema_ref: `${PAYLOAD_BASE}/tool-result.json`,
        payload: { tool, ...result },
      });
    }
  }

  /** Vendor-namespace checkpoint: applies a pending cancel or completes the run. */
  advance(runKey: string): RunRecord {
    const record = this.require(runKey);
    if (record.state !== "running") {
      return record;
    }
    if (record.cancelRequested) {
      this.emit(record, {
        kind: "event",
        event_id: "evt-cancelled",
        payload_schema_ref: `${PAYLOAD_BASE}/run-cancelled.json`,
        payload: { reason: "cancel_requested" },
      });
      record.state = "cancelled";
    } else {
      this.emit(record, {
        kind: "event",
        event_id: "evt-completed",
        payload_schema_ref: `${PAYLOAD_BASE}/run-completed.json`,
        payload: { outcome: "succeeded" },
      });
      record.state = "succeeded";
    }
    return record;
  }

  requestCancel(runKey: string): { state: "requested" | "rejected"; runRef: Ref } {
    const record = this.require(runKey);
    if (record.state === "running") {
      record.cancelRequested = true;
      return { state: "requested", runRef: record.runRef };
    }
    return { state: "rejected", runRef: record.runRef };
  }

  require(runKey: string): RunRecord {
    const record = this.runs.get(runKey);
    if (!record) {
      throw new UnknownRunError();
    }
    return record;
  }

  artifacts(runKey: string): Ref[] {
    const record = this.require(runKey);
    return [{ kind: "artifact", issuer_domain: "pilot-ts", id: `art-${record.runRef.id}` }];
  }
}
