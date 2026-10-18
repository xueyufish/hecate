import { describe, expect, it } from "vitest";

import {
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
