/**
 * Minimal transport-layer identity check per the security-claims schema:
 * the bearer token's JWT payload must carry iss/aud/sub/tenant/exp, and aud
 * must name this backend. No signature verification - the pilot is a loopback
 * verification asset (plan step7 owns real credential verification).
 *
 * Self-asserted identity or role fields (in the JWT payload or the request
 * body) are ignored entirely: authorization is bound to the verified claim
 * set, never to body content.
 */
export const EXPECTED_AUDIENCE = "pilot-backend";

export interface Claims {
  iss: string;
  aud: string;
  sub: string;
  tenant: string;
  exp: string;
  [extra: string]: unknown;
}

export type ClaimsResult =
  | { ok: true; claims: Claims }
  | { ok: false; reason: string };

function base64UrlDecode(segment: string): Buffer {
  return Buffer.from(segment, "base64url");
}

export function verifyClaims(authorizationHeader: string | undefined, audience = EXPECTED_AUDIENCE): ClaimsResult {
  if (!authorizationHeader || !authorizationHeader.startsWith("Bearer ")) {
    return { ok: false, reason: "missing bearer token" };
  }
  const token = authorizationHeader.slice("Bearer ".length).trim();
  const parts = token.split(".");
  if (parts.length !== 3) {
    return { ok: false, reason: "malformed token" };
  }
  let payload: unknown;
  try {
    payload = JSON.parse(base64UrlDecode(parts[1]!).toString("utf8"));
  } catch {
    return { ok: false, reason: "undecodable token payload" };
  }
  if (payload === null || typeof payload !== "object") {
    return { ok: false, reason: "token payload is not an object" };
  }
  const claims = payload as Record<string, unknown>;
  for (const field of ["iss", "aud", "sub", "tenant", "exp"] as const) {
    const value = claims[field];
    if (typeof value !== "string" || value.length === 0) {
      return { ok: false, reason: `missing or blank claim: ${field}` };
    }
  }
  if (claims.aud !== audience) {
    return { ok: false, reason: "audience mismatch" };
  }
  const expSeconds = Date.parse(claims["exp"] as string);
  if (Number.isNaN(expSeconds)) {
    return { ok: false, reason: "unparseable exp" };
  }
  if (expSeconds <= Date.now()) {
    return { ok: false, reason: "token expired" };
  }
  return { ok: true, claims: claims as Claims };
}
