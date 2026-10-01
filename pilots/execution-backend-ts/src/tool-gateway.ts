/** Pilot-only readonly tool receiver. Separate loopback HTTP boundary. */
import { createServer, type Server } from "node:http";
import { readJsonBody, sendBindingProblem, send } from "./problem.js";
import { verifyClaims } from "./auth.js";

export const TOOL_AUDIENCE = "pilot-tool-callback";

export function callbackToken(tenant: string): string {
  const header = Buffer.from(JSON.stringify({ alg: "none", typ: "JWT" })).toString("base64url");
  const claims = Buffer.from(JSON.stringify({
    iss: "pilot-ts", aud: TOOL_AUDIENCE, sub: "pilot-ts-callback", tenant,
    exp: new Date(Date.now() + 60_000).toISOString(),
  })).toString("base64url");
  // Synthetic fixture only: never reuse the inbound platform token.
  return `${header}.${claims}.sig`;
}

export async function startToolGateway(): Promise<{ url: string; server: Server }> {
  const server = createServer((req, res) => {
    void (async () => {
      if (req.method !== "POST" || req.url !== "/echo") {
        sendBindingProblem(res, "route_not_found", 404);
        return;
      }
      if (!verifyClaims(req.headers.authorization, TOOL_AUDIENCE).ok) {
        sendBindingProblem(res, "authorization_required", 403);
        return;
      }
      const body = await readJsonBody(req) as { input: { text?: string } };
      const text = body.input.text ?? "";
      send(res, 200, text.length === 0
        ? { status: "business_rejected", reason: "empty echo text" }
        : { status: "ok", output: text, served_by: "loopback-tool-gateway" });
    })().catch(() => sendBindingProblem(res, "invalid_callback", 400));
  });
  await new Promise<void>((resolveReady) => server.listen(0, "127.0.0.1", resolveReady));
  const address = server.address();
  if (address === null || typeof address === "string") throw new Error("missing tool gateway port");
  return { url: `http://127.0.0.1:${address.port}/echo`, server };
}

export function trustedCallbackUrl(value: string): string {
  const url = new URL(value);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.username || url.password) {
    throw new Error("pilot callback endpoint must be configured on loopback");
  }
  return url.href;
}
