/** Validate inbound wire bodies against the published authority, not a copy. */
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { Ajv2020 } from "ajv/dist/2020.js";
import { PILOT_ROOT, SCHEMAS_DIR } from "./paths.js";

const ajv = new Ajv2020({ strict: false, allErrors: true });
const ids = new Map<string, string>();
for (const file of readdirSync(SCHEMAS_DIR).filter((name) => name.endsWith(".schema.json"))) {
  const schema = JSON.parse(readFileSync(resolve(SCHEMAS_DIR, file), "utf8")) as { $id: string };
  ajv.addSchema(schema, schema.$id);
  ids.set(file.replace(".schema.json", ""), schema.$id);
}
export const validateRequest = ajv.compile({ $ref: ids.get("execution-request")! });
export const validateEchoParameters = ajv.compile(JSON.parse(readFileSync(resolve(PILOT_ROOT, "contracts/echo.input.schema.json"), "utf8")));
export const validateEchoOutput = ajv.compile(JSON.parse(readFileSync(resolve(PILOT_ROOT, "contracts/echo.output.schema.json"), "utf8")));
