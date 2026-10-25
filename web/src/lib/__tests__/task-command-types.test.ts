// taskCommandsApi builds only the paged list path (never the lazy-expiry
// single-command read); the cancel issue path is the full-command endpoint
// with a caller-supplied stable id and revision; label maps are the single
// wording home with raw fallback for unknown values.

import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ api: apiMock }));

import {
  commandKindLabel,
  commandStateLabel,
  commandStateMeaning,
  taskCommandsApi,
} from "@/lib/task-command-types";

beforeEach(() => {
  apiMock.get.mockReset();
  apiMock.post.mockReset();
});

describe("taskCommandsApi", () => {
  it("requests the list endpoint with default paging", () => {
    taskCommandsApi.list("task-1");
    expect(apiMock.get).toHaveBeenCalledWith("/api/tasks/task-1/commands?limit=20&offset=0");
  });

  it("serializes custom limit and offset", () => {
    taskCommandsApi.list("task-2", 50, 100);
    expect(apiMock.get).toHaveBeenCalledWith("/api/tasks/task-2/commands?limit=50&offset=100");
  });

  it("never builds the single-command read path", () => {
    for (const limit of [1, 20, 100]) {
      for (const offset of [0, 20, 2000]) {
        taskCommandsApi.list("task-3", limit, offset);
      }
    }
    for (const path of apiMock.get.mock.calls.map((call) => call[0] as string)) {
      expect(path).toMatch(/^\/api\/tasks\/task-3\/commands\?/);
      expect(path).not.toContain("/api/commands/");
    }
  });
});

describe("taskCommandsApi.issueCancel", () => {
  it("posts the full-command endpoint with kind, the caller's id and revision", () => {
    taskCommandsApi.issueCancel("task-9", "cmd-fixed", 4);
    expect(apiMock.post).toHaveBeenCalledTimes(1);
    expect(apiMock.post).toHaveBeenCalledWith("/api/tasks/task-9/commands", {
      kind: "cancel",
      command_id: "cmd-fixed",
      expected_revision: 4,
    });
  });

  it("never builds the /cancel shorthand path", () => {
    taskCommandsApi.issueCancel("task-9", "cmd-fixed", 4);
    for (const call of apiMock.post.mock.calls) {
      expect(call[0]).toBe("/api/tasks/task-9/commands");
      expect(String(call[0])).not.toMatch(/\/cancel$/);
    }
  });
});

describe("command label maps", () => {
  it("maps the five recorded states to fixed wording", () => {
    expect(commandStateLabel("requested")).toBe("已请求");
    expect(commandStateLabel("acknowledged")).toBe("已确认接收");
    expect(commandStateLabel("applied")).toBe("已应用");
    expect(commandStateLabel("rejected")).toBe("已拒绝");
    expect(commandStateLabel("expired")).toBe("已过期");
  });

  it("never presents requested or acknowledged as applied", () => {
    for (const state of ["requested", "acknowledged"]) {
      expect(commandStateLabel(state)).not.toContain("已生效");
      expect(commandStateLabel(state)).not.toBe("已应用");
    }
  });

  it("falls back to the raw value for unknown states and nullish input", () => {
    expect(commandStateLabel("future_state")).toBe("future_state");
    expect(commandStateLabel(null)).toBe("未记录");
    expect(commandStateLabel(undefined)).toBe("未记录");
  });

  it("maps command kinds with raw fallback", () => {
    expect(commandKindLabel("cancel")).toBe("取消");
    expect(commandKindLabel("pause")).toBe("暂停");
    expect(commandKindLabel("resume")).toBe("恢复");
    expect(commandKindLabel("provide_input")).toBe("补充输入");
    expect(commandKindLabel("mystery")).toBe("mystery");
    expect(commandKindLabel(null)).toBe("未记录");
  });
});

describe("command state meanings", () => {
  it("explains each recorded state without overclaiming effect", () => {
    expect(commandStateMeaning("requested")).toContain("尚未确认");
    expect(commandStateMeaning("acknowledged")).toContain("不证明");
    expect(commandStateMeaning("applied")).toContain("重新查询");
    expect(commandStateMeaning("rejected")).toContain("拒绝");
    expect(commandStateMeaning("expired")).toContain("过期");
  });

  it("returns null for unknown or missing states instead of guessing", () => {
    expect(commandStateMeaning("future_state")).toBeNull();
    expect(commandStateMeaning(null)).toBeNull();
    expect(commandStateMeaning(undefined)).toBeNull();
  });
});

it.each(["constructor", "__proto__", "toString", "hasOwnProperty"])(
  "preserves prototype property %s as an unknown value",
  (value) => {
    expect(commandStateLabel(value)).toBe(value);
    expect(commandKindLabel(value)).toBe(value);
  },
);
