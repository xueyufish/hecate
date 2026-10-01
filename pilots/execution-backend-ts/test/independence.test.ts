/**
 * A-side independence proof: the pilot's runtime and test dependency surface
 * contains no Hecate package and spawns no Python. Assertions read the
 * package manifest and scan the built source for forbidden references.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { PILOT_ROOT } from "./helpers.js";

function collectSourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...collectSourceFiles(full));
    } else if (entry.endsWith(".ts")) {
      out.push(full);
    }
  }
  return out;
}

describe("pilot independence from Hecate Python", () => {
  it("declares only the pinned dependency surface", () => {
    const pkg = JSON.parse(readFileSync(resolve(PILOT_ROOT, "package.json"), "utf8")) as {
      dependencies: Record<string, string>;
      devDependencies: Record<string, string>;
    };
    expect(Object.keys(pkg.dependencies).sort()).toEqual(["ajv"]);
    expect(Object.keys(pkg.devDependencies).sort()).toEqual([
      "@types/node",
      "typescript",
      "vitest",
    ]);
  });

  it("contains no hecate-python import or python subprocess reference", () => {
    const sourceFiles = collectSourceFiles(resolve(PILOT_ROOT, "src"));
    for (const file of sourceFiles) {
      const text = readFileSync(file, "utf8");
      expect(text.match(/\bfrom\s+["']hecate[\b."']/), file).toBeNull();
      expect(text.match(/import\s+hecate/), file).toBeNull();
      expect(text.includes("python"), file).toBe(false);
    }
  });
});
