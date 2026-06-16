"use client";

import { memo } from "react";
import {
  Bot,
  GitBranch,
  Wrench,
  Users,
  Search,
  Variable,
  Play,
  Square,
  GitMerge,
  GitFork,
} from "lucide-react";

interface NodeProps {
  data: {
    label: string;
    type: string;
    config: Record<string, unknown>;
  };
}

const TYPE_STYLES: Record<
  string,
  { bg: string; border: string; icon: React.ReactNode }
> = {
  conversation: {
    bg: "bg-blue-50",
    border: "border-blue-300",
    icon: <Bot className="h-4 w-4 text-blue-600" />,
  },
  condition: {
    bg: "bg-yellow-50",
    border: "border-yellow-300",
    icon: <GitBranch className="h-4 w-4 text-yellow-600" />,
  },
  "tool-call": {
    bg: "bg-purple-50",
    border: "border-purple-300",
    icon: <Wrench className="h-4 w-4 text-purple-600" />,
  },
  agent: {
    bg: "bg-green-50",
    border: "border-green-300",
    icon: <Users className="h-4 w-4 text-green-600" />,
  },
  "knowledge-retrieval": {
    bg: "bg-cyan-50",
    border: "border-cyan-300",
    icon: <Search className="h-4 w-4 text-cyan-600" />,
  },
  "variable-set": {
    bg: "bg-orange-50",
    border: "border-orange-300",
    icon: <Variable className="h-4 w-4 text-orange-600" />,
  },
  "fan-out": {
    bg: "bg-indigo-50",
    border: "border-indigo-300",
    icon: <GitFork className="h-4 w-4 text-indigo-600" />,
  },
  merge: {
    bg: "bg-slate-50",
    border: "border-slate-300",
    icon: <GitMerge className="h-4 w-4 text-slate-600" />,
  },
};

export const ConversationNode = memo(function ConversationNode(props: NodeProps) {
  return <WorkflowNodeBase {...props} typeKey="conversation" />;
});
export const ConditionNode = memo(function ConditionNode(props: NodeProps) {
  return <WorkflowNodeBase {...props} typeKey="condition" />;
});
export const ToolCallNode = memo(function ToolCallNode(props: NodeProps) {
  return <WorkflowNodeBase {...props} typeKey="tool-call" />;
});
export const AgentNode = memo(function AgentNode(props: NodeProps) {
  const invocationMode = props.data.config?.invocation_mode as string | undefined;
  const agentId = props.data.config?.agent_id as string | undefined;
  return (
    <WorkflowNodeBase {...props} typeKey="agent">
      <div className="flex flex-col gap-0.5 mt-1">
        {agentId && (
          <span className="text-[10px] text-muted-foreground font-mono truncate">
            ID: {agentId.slice(0, 8)}...
          </span>
        )}
        {invocationMode && invocationMode !== "direct" && (
          <span className="inline-flex self-start rounded bg-purple-100 px-1.5 py-0.5 text-[10px] font-medium text-purple-700">
            {invocationMode === "tool" ? "Tool Mode" : invocationMode}
          </span>
        )}
      </div>
    </WorkflowNodeBase>
  );
});
export const KnowledgeRetrievalNode = memo(function KnowledgeRetrievalNode(
  props: NodeProps
) {
  return <WorkflowNodeBase {...props} typeKey="knowledge-retrieval" />;
});
export const VariableSetNode = memo(function VariableSetNode(props: NodeProps) {
  return <WorkflowNodeBase {...props} typeKey="variable-set" />;
});
export const FanOutNode = memo(function FanOutNode(props: NodeProps) {
  const branches = props.data.config?.branches as string[] | undefined;
  const count = branches?.length || 0;
  return (
    <WorkflowNodeBase {...props} typeKey="fan-out">
      {count > 0 && (
        <span className="inline-flex self-start rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700">
          ×{count}
        </span>
      )}
    </WorkflowNodeBase>
  );
});
export const MergeNode = memo(function MergeNode(props: NodeProps) {
  return <WorkflowNodeBase {...props} typeKey="merge" />;
});

export const StartNode = memo(function StartNode() {
  return (
    <div className="flex flex-col items-center">
      <div className="flex h-10 w-10 items-center justify-center rounded-full border-2 border-green-400 bg-green-100">
        <Play className="h-4 w-4 text-green-600" />
      </div>
      <span className="mt-1 text-xs text-muted-foreground">Start</span>
    </div>
  );
});

export const EndNode = memo(function EndNode() {
  return (
    <div className="flex flex-col items-center">
      <div className="flex h-10 w-10 items-center justify-center rounded-full border-2 border-red-400 bg-red-100">
        <Square className="h-4 w-4 text-red-600" />
      </div>
      <span className="mt-1 text-xs text-muted-foreground">End</span>
    </div>
  );
});

function WorkflowNodeBase({
  data,
  typeKey,
  children,
}: NodeProps & { typeKey: string; children?: React.ReactNode }) {
  const style = TYPE_STYLES[typeKey] || TYPE_STYLES["conversation"];
  return (
    <div
      className={`min-w-[140px] rounded-md border px-3 py-2 shadow-sm ${style.bg} ${style.border}`}
    >
      <div className="flex items-center gap-2">
        {style.icon}
        <span className="text-sm font-medium">{data.label}</span>
      </div>
      {children}
    </div>
  );
}

export const nodeTypeComponents = {
  conversation: ConversationNode,
  condition: ConditionNode,
  "tool-call": ToolCallNode,
  agent: AgentNode,
  "knowledge-retrieval": KnowledgeRetrievalNode,
  "variable-set": VariableSetNode,
  "fan-out": FanOutNode,
  merge: MergeNode,
  start: StartNode,
  end: EndNode,
};
