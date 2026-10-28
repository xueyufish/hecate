// SSE frame parser: grammar coverage at the parser level. The stream-level
// behavior (chunked transport through ApiClient) is covered in
// api-client-auth and run-events-live tests.

import { describe, expect, it } from "vitest";

import { SseFrameParser } from "@/lib/sse-frames";

describe("SseFrameParser", () => {
  it("dispatches a terminated default frame", () => {
    const parser = new SseFrameParser();
    expect(parser.push('data: {"a":1}\n\n')).toEqual([{ event: null, data: '{"a":1}' }]);
  });

  it("keeps an unterminated frame until its blank line arrives", () => {
    const parser = new SseFrameParser();
    expect(parser.push('data: {"a":1}\n')).toEqual([]);
    expect(parser.push("data: tail\n\n")).toEqual([{ event: null, data: '{"a":1}\ntail' }]);
  });

  it("returns multiple frames completed by one chunk", () => {
    const parser = new SseFrameParser();
    const frames = parser.push("data: one\n\ndata: two\n\n");
    expect(frames.map((f) => f.data)).toEqual(["one", "two"]);
  });

  it("accepts CRLF and lone CR terminators", () => {
    const parser = new SseFrameParser();
    const crlf = parser.push("data: crlf\r\n\r\n");
    expect(crlf).toEqual([{ event: null, data: "crlf" }]);
    const cr = parser.push("data: cr\r\r");
    expect(cr).toEqual([{ event: null, data: "cr" }]);
  });

  it("joins multi-line data with newlines and strips one space after the colon", () => {
    const parser = new SseFrameParser();
    const frames = parser.push("data:line one\ndata:  line two\n\n");
    // "data:  line two" carries two spaces; the grammar strips exactly one.
    expect(frames).toEqual([{ event: null, data: "line one\n line two" }]);
  });

  it("carries the event field alongside data", () => {
    const parser = new SseFrameParser();
    const frames = parser.push("event: error\ndata: {\"code\":403}\n\n");
    expect(frames).toEqual([{ event: "error", data: '{"code":403}' }]);
  });

  it("ignores comments, unknown fields and frames with no content", () => {
    const parser = new SseFrameParser();
    const frames = parser.push(": keep-alive\ndata: real\n\nid: 7\nretry: 100\n\n");
    expect(frames).toEqual([{ event: null, data: "real" }]);
  });

  it("treats a field-only line (no colon) as an empty value", () => {
    const parser = new SseFrameParser();
    expect(parser.push("data\n\n")).toEqual([{ event: null, data: "" }]);
  });

  it("reports an unterminated tail at EOF without pretending it was delivered", () => {
    const parser = new SseFrameParser();
    parser.push('data: {"a":1}\n\n');
    parser.push("data: half");
    const pending = parser.finish();
    expect(pending).toEqual({ frame: { event: null, data: "half" }, terminated: false });
    expect(parser.finish()).toBeNull();
  });

  it("returns null at a clean EOF frame boundary", () => {
    const parser = new SseFrameParser();
    parser.push("data: full\n\n");
    expect(parser.finish()).toBeNull();
  });
});
