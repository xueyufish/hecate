/**
 * A-side sample validation: every standard sample in the repository is
 * validated against its published schema with ajv (draft 2020-12), entirely
 * in-process - no Hecate Python code anywhere in this path. The HTTP sample
 * pairs get dedicated handling: request/response bodies are checked against
 * their side's schema, and the negative pairs assert forbidden shapes.
 */
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { Ajv2020, type ValidateFunction } from "ajv/dist/2020";
import { describe, expect, it } from "vitest";
import { SAMPLES_DIR, SCHEMAS_DIR } from "./helpers.js";

const ajv = new Ajv2020({ strict: false, allErrors: true });

/** Register every published schema by its $id so cross-file $refs resolve. */
const SCHEMA_IDS = new Map<string, string>();
for (const file of readdirSync(SCHEMAS_DIR).filter((f) => f.endsWith(".schema.json"))) {
  const schema = JSON.parse(readFileSync(resolve(SCHEMAS_DIR, file), "utf8")) as { $id: string };
  ajv.addSchema(schema as object, schema.$id);
  SCHEMA_IDS.set(file.replace(".schema.json", ""), schema.$id);
}

function schemaValidator(name: string): ValidateFunction {
  const id = SCHEMA_IDS.get(name);
  if (!id) {
    throw new Error(`unknown schema: ${name}`);
  }
  return ajv.compile({ $ref: id });
}

function refValidator(schemaId: string, def: string): ValidateFunction {
  return ajv.compile({ $ref: `${schemaId}#/$defs/${def}` });
}

function sampleNames(dir: string): string[] {
  return readdirSync(resolve(SAMPLES_DIR, dir)).filter((f) => f.endsWith(".json"));
}

function loadJson(...segments: string[]): Record<string, unknown> {
  return JSON.parse(readFileSync(resolve(SAMPLES_DIR, ...segments), "utf8")) as Record<string, unknown>;
}

// Single-schema mapping, mirroring the Python side's governance set.
const SINGLE_SCHEMA_MAP: Array<[string, string]> = [
  ["capabilities", "capabilities"],
  ["errors", "errors"],
  ["events", "event-envelope"],
  ["requests", "execution-request"],
  ["tools", "tool"],
  ["manifest", "artifact-manifest"],
];

const REFERENCE_SAMPLES: Record<string, string> = {
  "artifact.json": "artifactRef",
  "authorization.json": "authorizationRef",
  "deployment.json": "deploymentRef",
  "platform-run.json": "runRef",
  "platform-task.json": "taskRef",
  "vendor-session.json": "sessionRef",
  "vendor-turn.json": "turnRef",
};

const SANDBOX_SAMPLES: Record<string, string> = {
  "create-environment.json": "createEnvironmentRequest",
  "sandbox-info.json": "sandboxInfo",
  "command-record-completed.json": "commandRecord",
  "command-unknown.json": "commandRecord",
};

const refsSchemaId = SCHEMA_IDS.get("references")!;
const claimsValidate = schemaValidator("security-claims");
const requestValidate = schemaValidator("execution-request");
const receiptValidate = schemaValidator("submit-receipt");
const cancelValidate = schemaValidator("cancel-receipt");
const eventPageValidate = schemaValidator("event-page");
const runStatusValidate = schemaValidator("run-status");

describe("standard samples validate against published schemas", () => {
  for (const [dir, schemaName] of SINGLE_SCHEMA_MAP) {
    it(`validates ${dir}/* against ${schemaName}.schema.json`, () => {
      const validate = schemaValidator(schemaName);
      for (const file of sampleNames(dir)) {
        const sample = loadJson(dir, file);
        const valid = validate(sample);
        expect(valid, `${dir}/${file}: ${JSON.stringify(validate.errors)}`).toBe(true);
      }
    });
  }

  it("validates references/* against the reference $defs", () => {
    for (const [file, def] of Object.entries(REFERENCE_SAMPLES)) {
      const sample = loadJson("references", file);
      const validate = refValidator(refsSchemaId, def);
      const valid = validate(sample);
      expect(valid, `references/${file}: ${JSON.stringify(validate.errors)}`).toBe(true);
    }
  });

  it("validates sandbox/* against the sandbox $defs", () => {
    const sandboxId = SCHEMA_IDS.get("sandbox")!;
    for (const [file, def] of Object.entries(SANDBOX_SAMPLES)) {
      const sample = loadJson("sandbox", file);
      const validate = refValidator(sandboxId, def);
      const valid = validate(sample);
      expect(valid, `sandbox/${file}: ${JSON.stringify(validate.errors)}`).toBe(true);
    }
  });

  it("validates security-claims positives and rejects the negative", () => {
    expect(claimsValidate(loadJson("security-claims", "valid-claim-set.json"))).toBe(true);
    expect(claimsValidate(loadJson("security-claims", "short-lived-claim-set.json"))).toBe(true);
    const negative = loadJson("security-claims", "negative-missing-aud.json");
    const valid = claimsValidate(negative);
    expect(valid, "negative-missing-aud.json must fail its schema").toBe(false);
  });

  it("validates HTTP pair request bodies (POST /runs) and typed response bodies", () => {
    for (const file of sampleNames("http")) {
      const pair = loadJson("http", file);
      const request = pair["request"] as Record<string, unknown>;
      const response = pair["response"] as Record<string, unknown>;
      const path = request["path"] as string;
      const status = response["status"] as number;
      const responseBody = response["body"] as Record<string, unknown> | null;
      const requestBody = request["body"] as Record<string, unknown> | null;
      if (path === "/runs" && request["method"] === "POST" && requestBody) {
        const valid = requestValidate(requestBody);
        expect(valid, `${file} request body: ${JSON.stringify(requestValidate.errors)}`).toBe(true);
      }
      if (status === 202 && path === "/runs" && responseBody) {
        expect(receiptValidate(responseBody), `${file} submit receipt`).toBe(true);
      }
      if (status === 202 && path.endsWith("/cancel") && responseBody) {
        expect(cancelValidate(responseBody), `${file} cancel receipt`).toBe(true);
      }
      if (status === 200 && path.includes("/events") && responseBody) {
        expect(eventPageValidate(responseBody), `${file} event page`).toBe(true);
      }
      if (
        status === 200 &&
        path.includes("/runs/") &&
        !path.includes("/events") &&
        !path.includes("/artifacts") &&
        responseBody
      ) {
        expect(runStatusValidate(responseBody), `${file} run status`).toBe(true);
      }
    }
  });

  it("rejects negative HTTP pairs where caller-synthesized codes appear as backend errors", () => {
    for (const file of ["negative-outcome-unknown-as-error.json", "negative-unreachable-as-error.json"]) {
      const pair = loadJson("http", file);
      const body = (pair["response"] as Record<string, unknown>)["body"] as Record<string, unknown>;
      // The negative sample demonstrates the FORBIDDEN shape; assert it uses
      // a caller-synthesized code and therefore can never be produced by a
      // conforming backend.
      const code = body["code"];
      expect(["outcome_unknown", "unreachable"]).toContain(code);
    }
  });

  it("problem bodies in HTTP pairs map only the four returnable codes", () => {
    const returnable = new Set(["unsupported", "authorization_denied", "budget_exhausted", "version_conflict"]);
    for (const file of sampleNames("http")) {
      if (file.startsWith("negative-")) {
        // Negative pairs demonstrate FORBIDDEN shapes; covered above.
        continue;
      }
      const pair = loadJson("http", file);
      const response = pair["response"] as Record<string, unknown>;
      const status = response["status"] as number;
      const body = response["body"] as Record<string, unknown> | null;
      if (status >= 400 && body && typeof body["code"] === "string") {
        expect(returnable.has(body["code"] as string), `${file}`).toBe(true);
        expect(body["type"], `${file} type URI`).toBe(
          `https://hecate.dev/contracts/errors/${body["code"] as string}`,
        );
      }
    }
  });
});
