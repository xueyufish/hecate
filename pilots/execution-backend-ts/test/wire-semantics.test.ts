/**
 * A-side wire semantics: drive the REAL loopback server over HTTP and assert
 * the hard contract behaviors - idempotency two-state, cursor resume with
 * explicit gap markers, cancel REQUESTED != APPLIED, business rejection as a
 * tool RESULT event with the run continuing, structured UNSUPPORTED errors,
 * and identity handling. Every call is plain HTTP + JSON: no Hecate Python.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  call,
  makeSubmitBody,
  makeToken,
  runRefWire,
  startPilot,
  type PilotServer,
} from "./helpers.js";

let pilot: PilotServer;
const token = makeToken();

beforeAll(async () => {
  pilot = await startPilot();
});
afterAll(async () => {
  await pilot.stop();
});

async function submit(body: Record<string, unknown>, key: string): Promise<{
  status: number;
  body: Record<string, unknown>;
}> {
  const res = await call(pilot.baseUrl, "POST", "/runs", {
    token,
    body: { ...body, idempotency_key: key },
    headers: { "Idempotency-Key": key },
  });
  return { status: res.status, body: res.body as Record<string, unknown> };
}

describe("capability discovery", () => {
  it("declares three ownership axes with verification-bearing capability levels", async () => {
    const res = await call(pilot.baseUrl, "GET", "/capabilities", { token });
    expect(res.status).toBe(200);
    const body = res.body as Record<string, unknown>;
    const ownership = body["ownership"] as Record<string, string>;
    expect(ownership["harness"]).toBe("vendor");
    expect(ownership["environment"]).toBe("none");
    expect(ownership["tool_execution"]).toBe("backend");
    const capabilities = body["capabilities"] as Record<string, string>;
    for (const cap of ["pause", "resume", "export_context"]) {
      expect(capabilities[cap]).toBe("unsupported");
    }
  });

  it("rejects requests without a valid claim set (binding-level 403, finding F2)", async () => {
    const res = await call(pilot.baseUrl, "GET", "/capabilities", {});
    expect(res.status).toBe(403);
    const body = res.body as Record<string, unknown>;
    // /capabilities has no run context: the pilot answers with a binding-level
    // problem (no contract code). Registered as finding F2 in the report.
    expect(body["type"]).toBe("https://hecate.dev/contracts/binding/authorization_required");
    expect(body["code"]).toBeUndefined();
  });

  it("maps run-scoped authorization failures to contract authorization_denied", async () => {
    const res = await call(
      pilot.baseUrl,
      "GET",
      `/runs/${encodeURIComponent("pilot-ts/br-nope")}`,
      {},
    );
    expect(res.status).toBe(403);
    const body = res.body as Record<string, unknown>;
    expect(body["code"]).toBe("authorization_denied");
    expect(body["type"]).toBe("https://hecate.dev/contracts/errors/authorization_denied");
    expect((body["request_ref"] as Record<string, unknown>)["id"]).toBe("br-nope");
  });
});

describe("idempotent submit", () => {
  it("replays the same key with identical content to the original receipt", async () => {
    const body = makeSubmitBody();
    const first = await submit(body, "idem-a-1");
    expect(first.status).toBe(202);
    const second = await submit(body, "idem-a-1");
    expect(second.status).toBe(202);
    expect(second.body).toEqual(first.body);
  });

  it("returns version_conflict (409) for the same key with different content", async () => {
    const first = await submit(makeSubmitBody(), "idem-a-2");
    expect(first.status).toBe(202);
    const conflicting = await submit(
      makeSubmitBody({ input: { objective: "Different objective" } }),
      "idem-a-2",
    );
    expect(conflicting.status).toBe(409);
    expect(conflicting.body["code"]).toBe("version_conflict");
    const detail = conflicting.body["detail_ns"] as Record<string, unknown>;
    expect(detail["reason"]).toBe("idempotency_key_content_mismatch");
    expect(detail["original_run_ref"]).toEqual(first.body["run_ref"]);
  });

  it("rejects a request contract_version outside the support window", async () => {
    const res = await submit(makeSubmitBody({ contract_version: "9.9" }), "idem-a-3");
    expect(res.status).toBe(409);
    expect(res.body["code"]).toBe("version_conflict");
    const detail = res.body["detail_ns"] as Record<string, unknown>;
    expect(detail["reason"]).toBe("contract_version_outside_window");
  });
});

describe("event cursors, gaps, and the deterministic run", () => {
  it("pages events from an opaque cursor and resumes after a disconnect", async () => {
    const submitted = await submit(makeSubmitBody(), "idem-a-4");
    const runRef = submitted.body["run_ref"] as { issuer_domain: string; id: string };

    const page1 = await call(
      pilot.baseUrl,
      "GET",
      `/runs/${runRefWire(runRef)}/events?limit=2`,
      { token },
    );
    expect(page1.status).toBe(200);
    const p1 = page1.body as { events: unknown[]; next_cursor: string | null; has_more: boolean };
    expect(p1.events).toHaveLength(2);
    expect(p1.has_more).toBe(true);
    expect(p1.next_cursor).not.toBeNull();

    // "Disconnect": a fresh read resumes from the returned cursor.
    const page2 = await call(
      pilot.baseUrl,
      "GET",
      `/runs/${runRefWire(runRef)}/events?cursor=${encodeURIComponent(p1.next_cursor!)}`,
      { token },
    );
    const p2 = page2.body as { events: Array<Record<string, unknown>>; has_more: boolean };
    expect(p2.events[0]!["source_sequence"]).toBe(2);
    const kinds = p2.events.map((e) => e["kind"]);
    expect(kinds).toContain("event");
  });

  it("declares lost event ranges with an explicit gap marker", async () => {
    const body = makeSubmitBody({
      backend_config_ns: { pilot: { simulate_gap: true } },
    });
    const submitted = await submit(body, "idem-a-5");
    const runRef = submitted.body["run_ref"] as { issuer_domain: string; id: string };
    // Drive the deterministic checkpoint so the terminal event is emitted
    // (the lost tool_result stays lost; the gap marker remains on the wire).
    await call(pilot.baseUrl, "POST", `/pilot-ns/runs/${runRefWire(runRef)}/advance`, { token });
    const res = await call(pilot.baseUrl, "GET", `/runs/${runRefWire(runRef)}/events`, { token });
    const page = res.body as { events: Array<Record<string, unknown>> };
    const gap = page.events.find((e) => e["kind"] === "gap");
    expect(gap).toBeDefined();
    expect((gap!["gap"] as Record<string, number>)["from_sequence"]).toBe(2);
    expect((gap!["gap"] as Record<string, number>)["to_sequence"]).toBe(2);
    // The marker sits after the hole (sample convention: hole 5..6, marker 7).
    expect(gap!["source_sequence"]).toBe(3);
    // And a run that never emits the lost event still terminates.
    expect(page.events.some((e) => (e["payload"] as Record<string, unknown>)?.["outcome"] === "succeeded")).toBe(true);
  });

  it("completes the run at the vendor checkpoint (deterministic)", async () => {
    const submitted = await submit(makeSubmitBody(), "idem-a-6");
    const runRef = submitted.body["run_ref"] as { issuer_domain: string; id: string };
    const wire = runRefWire(runRef);

    const running = await call(pilot.baseUrl, "GET", `/runs/${wire}`, { token });
    expect((running.body as Record<string, unknown>)["state"]).toBe("running");

    const advanced = await call(pilot.baseUrl, "POST", `/pilot-ns/runs/${wire}/advance`, { token });
    expect(advanced.status).toBe(200);
    expect((advanced.body as Record<string, unknown>)["state"]).toBe("succeeded");
  });
});

describe("cancel two-state semantics", () => {
  it("keeps REQUESTED (receipt) distinct from APPLIED (event after checkpoint)", async () => {
    const submitted = await submit(makeSubmitBody(), "idem-a-7");
    const runRef = submitted.body["run_ref"] as { issuer_domain: string; id: string };
    const wire = runRefWire(runRef);

    const cancel = await call(pilot.baseUrl, "POST", `/runs/${wire}/cancel`, { token });
    expect(cancel.status).toBe(202);
    expect((cancel.body as Record<string, unknown>)["state"]).toBe("requested");

    // The run is NOT cancelled until the checkpoint applies it.
    const before = await call(pilot.baseUrl, "GET", `/runs/${wire}`, { token });
    expect((before.body as Record<string, unknown>)["state"]).toBe("running");

    await call(pilot.baseUrl, "POST", `/pilot-ns/runs/${wire}/advance`, { token });
    const after = await call(pilot.baseUrl, "GET", `/runs/${wire}`, { token });
    expect((after.body as Record<string, unknown>)["state"]).toBe("cancelled");

    const events = await call(pilot.baseUrl, "GET", `/runs/${wire}/events`, { token });
    const page = events.body as { events: Array<Record<string, unknown>> };
    const cancelledEvent = page.events.find(
      (e) => (e["payload"] as Record<string, unknown> | undefined)?.["reason"] === "cancel_requested",
    );
    expect(cancelledEvent).toBeDefined();
  });

  it("rejects a cancel against a terminal run instead of pretending", async () => {
    const submitted = await submit(makeSubmitBody(), "idem-a-8");
    const runRef = submitted.body["run_ref"] as { issuer_domain: string; id: string };
    const wire = runRefWire(runRef);
    await call(pilot.baseUrl, "POST", `/pilot-ns/runs/${wire}/advance`, { token });
    const cancel = await call(pilot.baseUrl, "POST", `/runs/${wire}/cancel`, { token });
    expect((cancel.body as Record<string, unknown>)["state"]).toBe("rejected");
  });
});

describe("tool failure layering", () => {
  it("rejects unknown tools pre-dispatch with a binding-level 400", async () => {
    const res = await submit(
      makeSubmitBody({ backend_config_ns: { tool: "create_ticket", parameters: { priority: 3 } } }),
      "idem-a-9",
    );
    expect(res.status).toBe(400);
    const body = res.body as Record<string, unknown>;
    expect(body["type"]).toBe("https://hecate.dev/contracts/binding/parameter_schema_mismatch");
    expect(body["code"]).toBeUndefined();
  });

  it("reports business rejection as a tool RESULT event and the run continues", async () => {
    const res = await submit(
      makeSubmitBody({ backend_config_ns: { tool: "echo", parameters: { text: "" } } }),
      "idem-a-10",
    );
    expect(res.status).toBe(202);
    const runRef = res.body["run_ref"] as { issuer_domain: string; id: string };
    const wire = runRefWire(runRef);
    await call(pilot.baseUrl, "POST", `/pilot-ns/runs/${wire}/advance`, { token });

    const events = await call(pilot.baseUrl, "GET", `/runs/${wire}/events`, { token });
    const page = events.body as { events: Array<Record<string, unknown>> };
    const rejection = page.events.find(
      (e) => (e["payload"] as Record<string, unknown> | undefined)?.["status"] === "business_rejected",
    );
    expect(rejection).toBeDefined();
    expect(rejection!["kind"]).toBe("event");
    // Run continued to a successful completion despite the business rejection.
    const status = await call(pilot.baseUrl, "GET", `/runs/${wire}`, { token });
    expect((status.body as Record<string, unknown>)["state"]).toBe("succeeded");
  });
});

describe("unsupported capability and identity handling", () => {
  it("returns a structured 501 problem for the unsupported stream view", async () => {
    const submitted = await submit(makeSubmitBody(), "idem-a-11");
    const runRef = submitted.body["run_ref"] as { issuer_domain: string; id: string };
    const res = await call(pilot.baseUrl, "GET", `/runs/${runRefWire(runRef)}/events:stream`, { token });
    expect(res.status).toBe(501);
    const body = res.body as Record<string, unknown>;
    expect(body["code"]).toBe("unsupported");
    expect((body["detail_ns"] as Record<string, unknown>)["capability"]).toBe("events_stream");
  });

  it("ignores self-asserted role fields in the request body", async () => {
    const body = makeSubmitBody({ role: "admin", is_platform_admin: true });
    const res = await submit(body, "idem-a-12");
    expect(res.status).toBe(202);
    // The response carries no authorization notion at all - identity comes
    // only from the verified claim set.
    expect(JSON.stringify(res.body)).not.toContain("admin");
  });

  it("rejects a claim set aimed at another audience", async () => {
    const res = await call(pilot.baseUrl, "GET", "/capabilities", {
      token: makeToken({ aud: "other-service" }),
    });
    expect(res.status).toBe(403);
    // Contextless route: binding-level problem (finding F2).
    expect((res.body as Record<string, unknown>)["type"]).toBe(
      "https://hecate.dev/contracts/binding/authorization_required",
    );
  });
});

describe("unknown run reference", () => {
  it("returns a binding-level 404 (registered contract gap F1)", async () => {
    const res = await call(
      pilot.baseUrl,
      "GET",
      `/runs/${encodeURIComponent("pilot-ts/br-does-not-exist")}`,
      { token },
    );
    expect(res.status).toBe(404);
    const body = res.body as Record<string, unknown>;
    expect(body["type"]).toBe("https://hecate.dev/contracts/binding/run_not_found");
    // It is NOT a contract error code - the binding defines none for this.
    expect(body["code"]).toBeUndefined();
  });
});

describe("Step3 review regressions", () => {
  it("refuses pause over HTTP without fabricating success", async () => {
    const submitted = await submit(makeSubmitBody(), "a-wire-pause");
    const ref = submitted.body.run_ref as { issuer_domain: string; id: string };
    const res = await call(pilot.baseUrl, "POST", `/runs/${runRefWire(ref)}/pause`, { token });
    expect(res.status).toBe(501);
    expect((res.body as Record<string, unknown>).code).toBe("unsupported");
    expect(res.headers["content-type"]).toBe("application/problem+json");
  });
  for (const overrides of [
    { run_ref: { kind: "session", issuer_domain: "vendor", id: "s" } },
    { input: null }, { budget: { max_tokens: -1 } }, { trace_correlation: {} },
    { backend_config_ns: { tool: "echo", parameters: { text: 42 } } },
  ]) {
    it(`rejects malformed wire content ${JSON.stringify(overrides)}`, async () => {
      const res = await submit(makeSubmitBody(overrides), `invalid-${JSON.stringify(overrides)}`);
      expect(res.status).toBe(400);
    });
  }

  it("rejects header/body idempotency disagreement", async () => {
    const res = await call(pilot.baseUrl, "POST", "/runs", {
      token, body: makeSubmitBody(), headers: { "Idempotency-Key": "different-key" },
    });
    expect(res.status).toBe(400);
    expect(res.headers["content-type"]).toBe("application/problem+json");
  });

  it("scopes idempotency and run access to transport identity", async () => {
    const body = makeSubmitBody({ idempotency_key: "shared-across-tenants" });
    const first = await submit(body, "shared-across-tenants");
    const second = await call(pilot.baseUrl, "POST", "/runs", {
      token: makeToken({ tenant: "tenant-2" }), body,
      headers: { "Idempotency-Key": "shared-across-tenants" },
    });
    expect(second.status).toBe(202);
    expect((second.body as Record<string, unknown>).run_ref).not.toEqual(first.body.run_ref);
    const ref = first.body.run_ref as { issuer_domain: string; id: string };
    for (const [method, suffix] of [["GET", ""], ["GET", "/events"], ["GET", "/artifacts"], ["POST", "/cancel"]]) {
      const res = await call(pilot.baseUrl, method!, `/runs/${runRefWire(ref)}${suffix}`, {
        token: makeToken({ tenant: "tenant-2" }),
      });
      expect(res.status).toBe(403);
    }
  });

  it("the readonly tool result is obtained across a callback endpoint", async () => {
    const submitted = await submit(makeSubmitBody({ backend_config_ns: { parameters: { text: "hello" } } }), "a-callback");
    const ref = submitted.body.run_ref as { issuer_domain: string; id: string };
    const res = await call(pilot.baseUrl, "GET", `/runs/${runRefWire(ref)}/events`, { token });
    const events = (res.body as { events: Array<{ payload: Record<string, unknown> }> }).events;
    expect(events.at(-1)!.payload.output).toBe("hello");
    expect(events.at(-1)!.payload.served_by).toBe("loopback-tool-gateway");
  });
});
