/**
 * Shared A-side helpers: server spawn with readiness handshake, a tiny
 * loopback HTTP client, and an unsigned-claims token builder (the pilot
 * verifies claim envelope + audience only; signature verification belongs to
 * plan step7).
 */
import { spawn, type ChildProcess } from "node:child_process";
import { request } from "node:http";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
export const PILOT_ROOT = resolve(here, "..");
export const REPO_ROOT = resolve(PILOT_ROOT, "..", "..");
export const SAMPLES_DIR = resolve(REPO_ROOT, "tests", "test_execution", "samples");
export const SCHEMAS_DIR = resolve(REPO_ROOT, "src", "hecate", "contracts", "schemas");

export interface PilotServer {
  baseUrl: string;
  process: ChildProcess;
  stop(): Promise<void>;
}

export async function startPilot(): Promise<PilotServer> {
  const proc = spawn(process.execPath, [resolve(PILOT_ROOT, "dist", "server.js"), "--port", "0"], {
    stdio: ["ignore", "pipe", "inherit"],
  });
  const port = await new Promise<number>((resolvePort, rejectPort) => {
    const timer = setTimeout(() => rejectPort(new Error("pilot server did not report readiness")), 15000);
    let buffered = "";
    proc.stdout!.on("data", (chunk: Buffer) => {
      buffered += chunk.toString("utf8");
      const match = /PILOT_LISTENING port=(\d+)/.exec(buffered);
      if (match) {
        clearTimeout(timer);
        resolvePort(Number.parseInt(match[1]!, 10));
      }
    });
    proc.on("exit", (code) => {
      clearTimeout(timer);
      rejectPort(new Error(`pilot server exited early with code ${code}`));
    });
  });
  return {
    baseUrl: `http://127.0.0.1:${port}`,
    process: proc,
    async stop() {
      proc.kill();
      await new Promise<void>((resolveExit) => proc.on("exit", () => resolveExit()));
    },
  };
}

export function makeToken(overrides: Record<string, unknown> = {}): string {
  const header = Buffer.from(JSON.stringify({ alg: "none", typ: "JWT" })).toString("base64url");
  const payload = Buffer.from(
    JSON.stringify({
      iss: "hecate-platform",
      aud: "pilot-backend",
      sub: "workload-1",
      tenant: "tenant-1",
      exp: new Date(Date.now() + 3600_000).toISOString(),
      ...overrides,
    }),
  ).toString("base64url");
  return `${header}.${payload}.sig`;
}

export interface WireResponse {
  status: number;
  headers: Record<string, string | string[] | undefined>;
  body: unknown;
}

export function call(
  baseUrl: string,
  method: string,
  path: string,
  options: { token?: string; body?: unknown; headers?: Record<string, string> } = {},
): Promise<WireResponse> {
  return new Promise((resolveCall, rejectCall) => {
    const payload = options.body === undefined ? null : JSON.stringify(options.body);
    const req = request(
      `${baseUrl}${path}`,
      {
        method,
        headers: {
          ...(payload !== null ? { "content-type": "application/json", "content-length": Buffer.byteLength(payload) } : {}),
          ...(options.token ? { authorization: `Bearer ${options.token}` } : {}),
          ...options.headers,
        },
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf8");
          resolveCall({
            status: res.statusCode ?? 0,
            headers: res.headers,
            body: raw.length > 0 ? (JSON.parse(raw) as unknown) : null,
          });
        });
      },
    );
    req.on("error", rejectCall);
    if (payload !== null) {
      req.write(payload);
    }
    req.end();
  });
}

/** Build a submit body mirroring the submit-minimal standard sample. */
export function makeSubmitBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    contract_version: "0.1",
    task_ref: { kind: "task", issuer_domain: "platform", id: "t-123" },
    run_ref: { kind: "run", issuer_domain: "platform", id: "r-456" },
    deployment_ref: { kind: "deployment", issuer_domain: "platform", id: "dep-1" },
    authorization_ref: { kind: "authorization", issuer_domain: "platform", id: "authz-7" },
    input: { objective: "Summarize the attached material" },
    idempotency_key: "idem-001",
    trace_correlation: { trace_id: "tr-777" },
    ...overrides,
  };
}

export function runRefWire(runRef: { issuer_domain: string; id: string }): string {
  return encodeURIComponent(`${runRef.issuer_domain}/${runRef.id}`);
}
