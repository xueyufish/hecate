"""Worker implementations for specialized node execution.

Production Workers for all node types in the execution engine:
- ConditionWorker: evaluates expressions for graph routing
- VariableSetWorker: writes values to channels
- KnowledgeWorker: queries knowledge bases via RuntimePort
- ToolWorker: executes tools with guard hooks
- AgentWorker: delegates to sub-agents via nested graph execution
- SuggestionWorker: generates opening remarks and follow-up suggestions
- LLMWorker: full conversation pre-processing + LLM invocation + streaming
- CoordinatorWorker (1.3.18): Magentic double-loop dynamic orchestration
  over a runtime-emitted TaskDAG; lives alongside the 7 static Worker
  classes because it owns the orchestrator-specific event emission and
  budget enforcement. The dispatched node type is ``NodeType.COORDINATOR``.
"""

from hecate_runtime.workers.agent_worker import AgentWorker
from hecate_runtime.workers.condition_worker import ConditionWorker
from hecate_runtime.workers.coordinator_worker import CoordinatorWorker
from hecate_runtime.workers.knowledge_worker import KnowledgeWorker
from hecate_runtime.workers.llm_worker import LLMWorker
from hecate_runtime.workers.suggestion_worker import SuggestionWorker
from hecate_runtime.workers.tool_worker import ToolWorker
from hecate_runtime.workers.variable_set_worker import VariableSetWorker

__all__ = [
    "AgentWorker",
    "ConditionWorker",
    "CoordinatorWorker",
    "KnowledgeWorker",
    "LLMWorker",
    "SuggestionWorker",
    "ToolWorker",
    "VariableSetWorker",
]
