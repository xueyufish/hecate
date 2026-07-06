import { describe, it, expect } from "vitest";

// Extract the getCapabilityBadges logic for testing
// (mirrors the function in settings/models/page.tsx)

interface ModelMetadata {
  modalities?: { input?: string[]; output?: string[] };
  capabilities?: Record<string, boolean>;
  limits?: { context?: number; output?: number };
}

function getCapabilityBadges(metadata?: ModelMetadata): string[] {
  if (!metadata) return [];
  const badges: string[] = [];
  const caps = metadata.capabilities || {};
  const mods = metadata.modalities || {};
  const limits = metadata.limits || {};
  for (const [k, v] of Object.entries(caps)) {
    if (v) badges.push(k);
  }
  if (mods.input?.includes("image")) badges.push("vision");
  if (mods.input?.includes("audio")) badges.push("audio");
  if (limits.context && limits.context >= 128000) badges.push(`${Math.floor(limits.context / 1000)}K`);
  return [...new Set(badges)];
}

describe("getCapabilityBadges", () => {
  it("returns empty array for undefined metadata", () => {
    expect(getCapabilityBadges(undefined)).toEqual([]);
  });

  it("returns empty array for empty metadata", () => {
    expect(getCapabilityBadges({})).toEqual([]);
  });

  it("extracts capability flags", () => {
    const result = getCapabilityBadges({
      capabilities: { reasoning: true, tool_call: true, streaming: false },
    });
    expect(result).toContain("reasoning");
    expect(result).toContain("tool_call");
    expect(result).not.toContain("streaming");
  });

  it("detects vision from image input modality", () => {
    const result = getCapabilityBadges({
      modalities: { input: ["text", "image"], output: ["text"] },
    });
    expect(result).toContain("vision");
  });

  it("detects audio from audio input modality", () => {
    const result = getCapabilityBadges({
      modalities: { input: ["text", "audio"], output: ["text"] },
    });
    expect(result).toContain("audio");
  });

  it("generates context size badge for large contexts", () => {
    const result = getCapabilityBadges({
      limits: { context: 128000 },
    });
    expect(result).toContain("128K");
  });

  it("does not generate context badge for small contexts", () => {
    const result = getCapabilityBadges({
      limits: { context: 4096 },
    });
    expect(result).not.toContain("4K");
  });

  it("deduplicates badges", () => {
    const result = getCapabilityBadges({
      capabilities: { vision: true },
      modalities: { input: ["text", "image"] },
    });
    const visionCount = result.filter((b) => b === "vision").length;
    expect(visionCount).toBe(1);
  });

  it("combines all badge sources", () => {
    const result = getCapabilityBadges({
      modalities: { input: ["text", "image", "audio"], output: ["text"] },
      capabilities: { reasoning: true, tool_call: true },
      limits: { context: 200000 },
    });
    expect(result).toContain("vision");
    expect(result).toContain("audio");
    expect(result).toContain("reasoning");
    expect(result).toContain("tool_call");
    expect(result).toContain("200K");
  });
});
