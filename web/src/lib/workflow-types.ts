import { z } from "zod";

/** Channel types matching engine ChannelType enum */
export const ChannelTypeSchema = z.enum([
  "last_value",
  "topic",
  "persistent_topic",
  "accumulator",
]);

/** Node types matching engine NodeType enum */
export const NodeTypeSchema = z.enum([
  "conversation",
  "tool-call",
  "condition",
  "agent",
  "knowledge-retrieval",
  "variable-set",
]);

/** Edge target can be a string node ID or a dict of route→nodeId for conditional edges */
export const EdgeTargetSchema = z.union([
  z.string(),
  z.record(z.string(), z.string()),
]);

/** Channel definition within graph state */
export const ChannelDefSchema = z.object({
  type: ChannelTypeSchema,
  default: z.unknown().optional(),
  initial: z.unknown().optional(),
  reduce: z.enum(["append", "add"]).optional(),
});

/** Node configuration properties */
export const NodeConfigSchema = z.object({
  model: z.string().optional(),
  system_prompt: z.string().optional(),
  tool_name: z.string().optional(),
  expression: z.string().optional(),
  agent_ref: z.string().optional(),
  skill_ref: z.string().optional(),
  kb_ids: z.array(z.string()).optional(),
  query_template: z.string().optional(),
  top_k: z.number().optional(),
  variable_name: z.string().optional(),
  value: z.unknown().optional(),
  channels: z
    .object({
      readable: z.array(z.string()).optional(),
      writable: z.array(z.string()).optional(),
    })
    .optional(),
});

/** Single node definition in the graph */
export const NodeDefSchema = z.object({
  type: NodeTypeSchema,
  config: NodeConfigSchema,
});

/** Single edge in the graph */
export const EdgeDefSchema = z.object({
  source: z.string().min(1),
  target: EdgeTargetSchema,
  trigger: z.string().nullable().optional(),
});

/** Full Graph DSL schema matching schemas/graph-dsl.schema.json */
export const GraphDSLSchema = z.object({
  version: z.literal("1.0"),
  name: z.string().min(1).max(255),
  state: z.record(z.string(), ChannelDefSchema),
  nodes: z.record(z.string(), NodeDefSchema),
  edges: z.array(EdgeDefSchema),
  entry: z.string().optional(),
});

export type GraphDSL = z.infer<typeof GraphDSLSchema>;
export type NodeType = z.infer<typeof NodeTypeSchema>;
export type ChannelType = z.infer<typeof ChannelTypeSchema>;
export type NodeDef = z.infer<typeof NodeDefSchema>;
export type EdgeDef = z.infer<typeof EdgeDefSchema>;
export type ChannelDef = z.infer<typeof ChannelDefSchema>;
export type NodeConfig = z.infer<typeof NodeConfigSchema>;

/** Validate raw DSL data, returning typed result or error list */
export function validateDsl(
  data: unknown
): { success: true; data: GraphDSL } | { success: false; errors: string[] } {
  const result = GraphDSLSchema.safeParse(data);
  if (result.success) {
    return { success: true, data: result.data };
  }
  const issues = result.error?.issues || [];
  const errors = issues.map(
    (issue: { path: PropertyKey[]; message: string }) =>
      `${issue.path.join(".")}: ${issue.message}`
  );
  return { success: false, errors };
}
