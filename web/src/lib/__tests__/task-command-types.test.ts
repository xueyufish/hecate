// taskCommandsApi builds only the paged list path (never the lazy-expiry
// single-command read); label maps are the single wording home with raw
// fallback for unknown values.

import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ api: apiMock }));

import {
  commandKindLabel,
  commandStateLabel,
  taskCommandsApi,
} from "@/lib/task-command-types";

beforeEach(() => {
  apiMock.get.mockReset();
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

it.each(["constructor", "__proto__", "toString", "hasOwnProperty"])(
  "preserves prototype property %s as an unknown value",
  (value) => {
    expect(commandStateLabel(value)).toBe(value);
    expect(commandKindLabel(value)).toBe(value);
  },
);
