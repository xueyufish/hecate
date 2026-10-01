/**
 * RFC 9457 problem+json builders following the binding's ProblemBody profile:
 * contract-mapped problems carry `type` = https://hecate.dev/contracts/errors/{code}
 * plus the contract error fields from errors.schema.json; binding-level
 * rejections (before the contract layer) use the /binding/ URI space and no
 * contract error code.
 */
import type { IncomingMessage, ServerResponse } from "node:http";

export type ContractErrorCode =
  | "unsupported"
  | "authorization_denied"
  | "budget_exhausted"
  | "version_conflict";

/** Codes that must NEVER appear as backend HTTP error responses. */
export const CALLER_SYNTHESIZED_CODES = new Set(["unreachable", "outcome_unknown"]);

export const HTTP_STATUS_BY_CODE: Record<ContractErrorCode, number> = {
  unsupported: 501,
  authorization_denied: 403,
  budget_exhausted: 429,
  version_conflict: 409,
};

export interface ContractProblemFields {
  code: ContractErrorCode;
  request_ref: object;
  message: string;
  detail_ns?: Record<string, unknown>;
}

function send(res: ServerResponse, status: number, payload: unknown, contentType = "application/json"): void {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": contentType,
    "x-contract-version": "0.1",
  });
  res.end(body);
}
export { send };

export function sendProblem(
  res: ServerResponse,
  code: ContractErrorCode,
  fields: Omit<ContractProblemFields, "code">,
): void {
  const status = HTTP_STATUS_BY_CODE[code];
  send(res, status, {
    type: `https://hecate.dev/contracts/errors/${code}`,
    title: code.replaceAll("_", " "),
    status,
    code,
    ...fields,
  }, "application/problem+json");
}

export function sendBindingProblem(
  res: ServerResponse,
  bindingType: string,
  status: number,
  extra: Record<string, unknown> = {},
): void {
  const { title = "Binding-level rejection", ...rest } = extra;
  send(res, status, {
    type: `https://hecate.dev/contracts/binding/${bindingType}`,
    title,
    status,
    ...rest,
  }, "application/problem+json");
}

export async function readJsonBody(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(chunk as Buffer);
  }
  const raw = Buffer.concat(chunks).toString("utf8");
  if (raw.length === 0) {
    return undefined;
  }
  return JSON.parse(raw) as unknown;
}
