/**
 * Repo source-file references (design D2): schemas and standard samples are
 * consumed from their authoritative locations in the repository, never
 * copied. A contract revision must land at the source; this pilot breaks
 * loudly instead of drifting.
 */
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

/** pilots/execution-backend-ts (works for both src/ and dist/ layouts). */
export const PILOT_ROOT = resolve(here, "..");
export const REPO_ROOT = resolve(PILOT_ROOT, "..", "..");
export const SCHEMAS_DIR = resolve(REPO_ROOT, "src", "hecate", "contracts", "schemas");
export const SAMPLES_DIR = resolve(REPO_ROOT, "tests", "test_execution", "samples");
export const OPENAPI_PATH = resolve(
  REPO_ROOT,
  "src",
  "hecate",
  "contracts",
  "openapi",
  "execution-backend.http.v0_1.yaml",
);

export function schemaPath(name: string): string {
  return resolve(SCHEMAS_DIR, `${name}.schema.json`);
}
