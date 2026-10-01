/**
 * Loopback HTTP server for the execution-backend contract (0.x draft).
 *
 * Scope guards (plan step3 pilot): binds loopback only, carries no
 * credentials, and the only tool is the side-effect-free echo. The vendor
 * namespace route (/pilot-ns/...) is the deterministic checkpoint that turns
 * REQUESTED cancels into APPLIED events and completes runs; it is pilot-local
 * and grants no platform permissions.
 */
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { verifyClaims, type Claims } from "./auth.js";
import { validateRequest, validateEchoParameters } from "./validation.js";
import { startToolGateway, trustedCallbackUrl } from "./tool-gateway.js";
import {
  readJsonBody,
  sendBindingProblem,
  sendProblem,
  send,
} from "./problem.js";
import {
  CONTRACT_VERSION,
  IdempotencyConflictError,
  RunStore,
  SUPPORTED_CONTRACT_VERSIONS,
  UnknownRunError,
  type Ref,
} from "./store.js";

const store = new RunStore();

function runRefFromWire(wire: string): Ref {
  const decoded = decodeURIComponent(wire);
  const slash = decoded.indexOf("/");
  if (slash <= 0) {
    throw new Error("malformed run reference");
  }
  return { kind: "run", issuer_domain: decoded.slice(0, slash), id: decoded.slice(slash + 1) };
}

function authorize(req: IncomingMessage, url: URL): { ok: true; claims: Claims } | { ok: false; reason: string; runRef?: Ref } {
  const result = verifyClaims(req.headers.authorization);
  if (result.ok) {
    return { ok: true, claims: result.claims };
  }
  // Run-scoped routes can reference the target run even when the caller is
  // unauthorized (finding F2: the binding leaves auth-error shapes undefined
  // for contextless routes - run-scoped 403s are contract problems, others
  // are binding-level).
  const runMatch = /^\/runs\/([^/]+)/.exec(url.pathname);
  if (runMatch) {
    try {
      return { ok: false, reason: result.reason, runRef: runRefFromWire(runMatch[1]!) };
    } catch {
      return { ok: false, reason: result.reason };
    }
  }
  return { ok: false, reason: result.reason };
}

function handleUnknownRun(res: ServerResponse, error: unknown): void {
  if (error instanceof UnknownRunError) {
    // Finding F1 (pilot report): the binding defines no response for unknown
    // run references; the pilot uses a binding-level 404 and registers the gap.
    sendBindingProblem(res, "run_not_found", 404, { title: "Unknown run reference" });
    return;
  }
  if (error instanceof IdempotencyConflictError) {
    sendProblem(res, "version_conflict", {
      message: error.message,
      request_ref: error.originalRunRef,
      detail_ns: {
        reason: "idempotency_key_content_mismatch",
        original_run_ref: error.originalRunRef,
      },
    });
    return;
  }
  sendBindingProblem(res, "internal_error", 500, { title: "Internal pilot error" });
}

function requireContractVersion(request: Record<string, unknown>, res: ServerResponse): boolean {
  const version = request["contract_version"];
  if (typeof version !== "string" || !SUPPORTED_CONTRACT_VERSIONS.has(version)) {
    sendProblem(res, "version_conflict", {
      message: "request contract_version is outside the support window",
      // errors.schema requires a request_ref on every contract error; the
      // caller-declared run reference is the best available target here.
      request_ref: typeof request["run_ref"] === "object" && request["run_ref"] !== null
        ? (request["run_ref"] as Ref)
        : { kind: "run", issuer_domain: "platform", id: "unknown" },
      detail_ns: {
        reason: "contract_version_outside_window",
        supported: [...SUPPORTED_CONTRACT_VERSIONS],
      },
    });
    return false;
  }
  return true;
}

async function handleSubmit(req: IncomingMessage, res: ServerResponse, claims: Claims, callbackUrl: string): Promise<void> {
  let body: unknown;
  try {
    body = await readJsonBody(req);
  } catch {
    sendBindingProblem(res, "request_schema_mismatch", 400, {
      title: "Request body is not valid JSON",
    });
    return;
  }
  if (body === null || typeof body !== "object" || Array.isArray(body)) {
    sendBindingProblem(res, "request_schema_mismatch", 400, {
      title: "Request body must be a JSON object",
    });
    return;
  }
  const request = body as Record<string, unknown>;
  if (!validateRequest(request)) {
    sendBindingProblem(res, "request_schema_mismatch", 400, {
      title: "Request body failed authoritative schema validation",
      errors: validateRequest.errors,
    });
    return;
  }
  if (!requireContractVersion(request, res)) {
    return;
  }

  // Pre-dispatch tool declaration check (binding-level 400 before the
  // contract layer; the pilot registers exactly one echo tool).
  const config = (request["backend_config_ns"] ?? {}) as Record<string, unknown>;
  const tool = config["tool"];
  if (tool !== undefined && tool !== "echo") {
    sendBindingProblem(res, "parameter_schema_mismatch", 400, {
      title: "Parameter validation failed",
      detail: "tool parameter does not match the declared input schema",
      errors: [{ tool, field: "tool", reason: "unknown tool; pilot registers only 'echo'" }],
    });
    return;
  }
  if (!validateEchoParameters(config.parameters ?? {})) {
    sendBindingProblem(res, "parameter_schema_mismatch", 400, {
      title: "Tool parameters failed schema validation", errors: validateEchoParameters.errors,
    });
    return;
  }

  const idempotencyKey = req.headers["idempotency-key"];
  if (typeof idempotencyKey !== "string" || idempotencyKey.length === 0) {
    sendBindingProblem(res, "request_schema_mismatch", 400, {
      title: "Missing Idempotency-Key header",
    });
    return;
  }
  if (idempotencyKey !== request.idempotency_key) {
    sendBindingProblem(res, "request_schema_mismatch", 400, {
      title: "Idempotency-Key header must match body idempotency_key",
    });
    return;
  }
  try {
    const outcome = store.submit(request, idempotencyKey, callerScope(claims));
    if (outcome.created) {
      await store.executeTool(outcome.record, request, callbackUrl, claims.tenant);
    }
    const { receipt } = outcome;
    send(res, 202, receipt);
  } catch (error) {
    handleUnknownRun(res, error);
  }
}

function handleEvents(url: URL, res: ServerResponse, runRef: Ref): void {
  const record = store.require(`${runRef.issuer_domain}/${runRef.id}`);
  const cursorParam = url.searchParams.get("cursor");
  let startSequence = 0;
  if (cursorParam !== null) {
    try {
      const decoded = Buffer.from(cursorParam, "base64url").toString("utf8");
      if (!decoded.startsWith("seq:")) {
        throw new Error("bad cursor");
      }
      if (!/^seq:\d+$/.test(decoded)) throw new Error("bad cursor");
      startSequence = Number(decoded.slice(4));
      if (!Number.isInteger(startSequence) || startSequence < 0) {
        throw new Error("bad cursor");
      }
    } catch {
      sendBindingProblem(res, "cursor_mismatch", 400, { title: "Malformed event cursor" });
      return;
    }
  }
  const limitParam = url.searchParams.get("limit");
  const limit = Math.min(Math.max(Number.parseInt(limitParam ?? "10", 10) || 10, 1), 100);
  const page = record.events.filter((e) => e.source_sequence >= startSequence).slice(0, limit);
  const last = page.length > 0 ? page[page.length - 1]!.source_sequence : startSequence - 1;
  const hasMore = record.events.some((e) => e.source_sequence > last);
  send(res, 200, {
    events: page,
    next_cursor: Buffer.from(`seq:${last + 1}`).toString("base64url"),
    has_more: hasMore,
  });
}

function callerScope(claims: Claims): string {
  return JSON.stringify([claims.iss, claims.tenant, claims.sub]);
}

export function createServerInstance(callbackUrl: string): Server {
  return createServer((req, res) => {
    void (async () => {
      const url = new URL(req.url ?? "/", "http://loopback");
      const path = url.pathname;

      const auth = authorize(req, url);
      if (!auth.ok) {
        if (auth.runRef) {
          sendProblem(res, "authorization_denied", {
            message: `transport claim check failed: ${auth.reason}`,
            request_ref: auth.runRef,
          });
        } else {
          sendBindingProblem(res, "authorization_required", 403, {
            title: "Missing or invalid transport claims",
            detail: auth.reason,
          });
        }
        return;
      }
      res.setHeader("x-contract-version", CONTRACT_VERSION);

      try {
        if (req.method === "GET" && path === "/capabilities") {
          send(res, 200, capabilitiesDocument());
          return;
        }
        if (req.method === "POST" && path === "/runs") {
          await handleSubmit(req, res, auth.claims, callbackUrl);
          return;
        }
        const runMatch = /^\/runs\/([^/]+)(?:\/(events|events:stream|cancel|artifacts|pause|resume|provide_input|resolve_approval|export_context))?$/.exec(path);
        if (runMatch) {
          const runRef = runRefFromWire(runMatch[1]!);
          const target = store.require(`${runRef.issuer_domain}/${runRef.id}`);
          if (target.callerScope !== callerScope(auth.claims)) {
            sendProblem(res, "authorization_denied", { message: "run belongs to another caller scope", request_ref: runRef });
            return;
          }
          const sub = runMatch[2];
          if (sub && ["pause", "resume", "provide_input", "resolve_approval", "export_context"].includes(sub) && req.method === "POST") {
            sendProblem(res, "unsupported", {
              message: `${sub} is not supported by this backend`,
              request_ref: runRef, detail_ns: { capability: sub },
            });
            return;
          }
          if (!sub && req.method === "GET") {
            const record = store.require(`${runRef.issuer_domain}/${runRef.id}`);
            send(res, 200, { run_ref: record.runRef, state: record.state, detail_ns: {} });
            return;
          }
          if (sub === "events" && req.method === "GET") {
            handleEvents(url, res, runRef);
            return;
          }
          if (sub === "events:stream" && req.method === "GET") {
            // Standing unsupported-capability negative: structured problem,
            // never a fabricated stream.
            sendProblem(res, "unsupported", {
              message: "event streaming is not supported by this backend",
              request_ref: runRef,
              detail_ns: { capability: "events_stream" },
            });
            return;
          }
          if (sub === "cancel" && req.method === "POST") {
            const outcome = store.requestCancel(`${runRef.issuer_domain}/${runRef.id}`);
            send(res, 202, { run_ref: outcome.runRef, state: outcome.state });
            return;
          }
          if (sub === "artifacts" && req.method === "GET") {
            send(res, 200, { artifacts: store.artifacts(`${runRef.issuer_domain}/${runRef.id}`) });
            return;
          }
        }
        if (req.method === "POST" && path.startsWith("/pilot-ns/runs/") && path.endsWith("/advance")) {
          const wire = path.slice("/pilot-ns/runs/".length, -"/advance".length);
          const runRef = runRefFromWire(wire);
          if (store.require(`${runRef.issuer_domain}/${runRef.id}`).callerScope !== callerScope(auth.claims)) {
            sendProblem(res, "authorization_denied", { message: "run belongs to another caller scope", request_ref: runRef });
            return;
          }
          const record = store.advance(`${runRef.issuer_domain}/${runRef.id}`);
          send(res, 200, { run_ref: record.runRef, state: record.state, detail_ns: {} });
          return;
        }
        sendBindingProblem(res, "route_not_found", 404, { title: "No such route" });
      } catch (error) {
        handleUnknownRun(res, error);
      }
    })().catch((error: unknown) => handleUnknownRun(res, error));
  });
}

function capabilitiesDocument(): Record<string, unknown> {
  return {
    contract_version: CONTRACT_VERSION,
    backend_type: "pilot-ts",
    ownership: { harness: "vendor", environment: "none", tool_execution: "backend" },
    capabilities: {
      provide_input: "unsupported",
      resolve_approval: "unsupported",
      pause: "unsupported",
      resume: "unsupported",
      export_context: "unsupported",
      cancel: "cooperative",
      events_resume: "cooperative",
      callback: "cooperative",
      tool_proxy: "unsupported",
      sandbox: "unsupported",
      internal_tools_visibility: "cooperative",
      subtask_tracking: "unsupported",
    },
    verification: Object.fromEntries(["cancel", "events_resume", "callback", "internal_tools_visibility"].map((name) => [name, {
      source: "loopback pilot contract tests",
      checked_at: "2026-10-01T00:00:00Z",
      contract_version: CONTRACT_VERSION,
      backend_version: "0.1.0",
      deployment_shape: "isolated_loopback",
      observation_source: "independent_test",
      evidence_ref: "docs/refactor/execution-backend-pilot-report.md",
      valid_until: "2026-10-02T00:00:00Z",
    }])),
    reconciliation_support: { query_by_vendor_session: false },
  };
}

const argvPort = process.argv.indexOf("--port");
const port = argvPort >= 0 ? Number.parseInt(process.argv[argvPort + 1] ?? "0", 10) : 0;
const argvCallback = process.argv.indexOf("--tool-callback-url");
const localGateway = argvCallback < 0 ? await startToolGateway() : null;
const callbackUrl = trustedCallbackUrl(localGateway?.url ?? process.argv[argvCallback + 1]!);
const server = createServerInstance(callbackUrl);
server.listen(port, "127.0.0.1", () => {
  const address = server.address();
  const actual = typeof address === "object" && address !== null ? address.port : port;
  process.stdout.write(`PILOT_LISTENING port=${actual}\n`);
});
