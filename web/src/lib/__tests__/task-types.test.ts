import { describe, expect, it } from "vitest";

import {
  acceptanceLabel,
  checkStatusLabel,
  digestPreview,
  lifecycleLabel,
  outcomeLabel,
  recoveryStateLabel,
  taskErrorCode,
  taskErrorMessage,
} from "../task-types";

describe("lifecycle labels", () => {
  it("maps known lifecycle states", () => {
    expect(lifecycleLabel("running")).toBe("执行中");
    expect(lifecycleLabel("succeeded")).toBe("已成功");
    expect(lifecycleLabel("unrecorded")).toBe("未记录");
  });

  it("maps missing lifecycle to 未记录", () => {
    expect(lifecycleLabel(null)).toBe("未记录");
    expect(lifecycleLabel(undefined)).toBe("未记录");
    expect(lifecycleLabel("")).toBe("未记录");
  });

  it("falls back to the raw string for unknown values (never swallowed)", () => {
    expect(lifecycleLabel("brand_new_state")).toBe("brand_new_state");
  });
});

describe("recovery state and outcome labels are separate vocabularies", () => {
  it("maps the four recovery states", () => {
    expect(recoveryStateLabel("claimed")).toBe("已领取");
    expect(recoveryStateLabel("outcome_unknown")).toBe("结果未知");
    expect(recoveryStateLabel("never_started")).toBe("未开始");
    expect(recoveryStateLabel("store_unavailable")).toBe("存储不可用");
  });

  it("maps recorded outcomes and labels missing outcome as 未记录", () => {
    expect(outcomeLabel("succeeded")).toBe("已成功");
    expect(outcomeLabel("failed")).toBe("失败");
    expect(outcomeLabel("unknown")).toBe("结果未知");
    expect(outcomeLabel(null)).toBe("未记录");
    expect(outcomeLabel(undefined)).toBe("未记录");
  });

  it("never renders execution semantics for claimed + succeeded", () => {
    // The claimed+succeeded combination must not surface "执行中" wording —
    // the claim is the recovery fact, the outcome is the recorded fact.
    const labels = [recoveryStateLabel("claimed"), outcomeLabel("succeeded")];
    const rendered = labels.join(" ");
    expect(rendered).not.toMatch(/执行中/);
    expect(rendered).toContain("已领取");
    expect(rendered).toContain("已成功");
  });
});

describe("error body consumption", () => {
  it("reads detail.error.code/message from the FastAPI envelope", () => {
    const err = { detail: { error: { code: "NOT_FOUND", message: "run not found" } } };
    expect(taskErrorCode(err)).toBe("NOT_FOUND");
    expect(taskErrorMessage(err)).toBe("run not found");
  });

  it("falls back to Error.message and a generic text", () => {
    expect(taskErrorMessage(new Error("network down"))).toBe("network down");
    expect(taskErrorMessage("weird")).toBe("请求失败");
    expect(taskErrorCode(undefined)).toBeNull();
  });

  it("does not trust top-level error fields outside the detail envelope", () => {
    const err = { error: { code: "SPOOF", message: "spoofed" } };
    expect(taskErrorCode(err)).toBeNull();
    expect(taskErrorMessage(err)).toBe("请求失败");
  });
});

describe("acceptance labels are a recorded-verdict vocabulary", () => {
  it("maps the four recorded statuses and the read-side refusal", () => {
    expect(acceptanceLabel("passed")).toBe("验收通过");
    expect(acceptanceLabel("acceptance_failed")).toBe("验收未通过");
    expect(acceptanceLabel("acceptance_unverified")).toBe("验收未能核实");
    expect(acceptanceLabel("not_declared")).toBe("未声明验收");
    expect(acceptanceLabel("uninterpretable")).toBe("记录无法解释");
  });

  it("labels missing as 未记录 and never swallows unknown statuses", () => {
    expect(acceptanceLabel(null)).toBe("未记录");
    expect(acceptanceLabel("brand_new_status")).toBe("brand_new_status");
  });
});

describe("check status labels", () => {
  it("maps recorded check verdicts", () => {
    expect(checkStatusLabel("passed")).toBe("通过");
    expect(checkStatusLabel("failed")).toBe("未通过");
    expect(checkStatusLabel("unverified")).toBe("未能核实");
    expect(checkStatusLabel(null)).toBe("未记录");
  });
});

describe("digest preview", () => {
  it("shortens long digests and keeps short ones whole", () => {
    expect(digestPreview("a".repeat(64))).toBe(`${"a".repeat(16)}…`);
    expect(digestPreview("short-digest")).toBe("short-digest");
  });

  it("reports missing digests as 未记录", () => {
    expect(digestPreview(null)).toBe("未记录");
    expect(digestPreview(undefined)).toBe("未记录");
    expect(digestPreview("")).toBe("未记录");
  });
});
