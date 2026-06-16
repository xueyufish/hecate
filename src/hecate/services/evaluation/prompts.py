"""LLM-as-Judge prompt templates for agent evaluation.

Each prompt template includes Python format-string placeholders that are
filled at evaluation time. The LLM is asked to return valid JSON with a
``score`` (float 0.0–1.0) and ``reasoning`` (string explanation).
"""

from __future__ import annotations

CORRECTNESS_PROMPT = """\
You are an expert evaluator. Compare the **Generated Answer** against the \
**Expected Answer** for the given **Query**.

Rate the factual correctness of the Generated Answer on a scale from 0.0 \
(completely wrong) to 1.0 (fully correct).

Consider:
- Are all facts in the Generated Answer accurate compared to the Expected Answer?
- Are there any hallucinated or unsupported claims?
- Is critical information missing or distorted?

**Query:** {query}
**Expected Answer:** {expected_answer}
**Generated Answer:** {answer}

Respond with ONLY valid JSON (no markdown, no commentary):
{{"score": <float 0.0-1.0>, "reasoning": "<brief explanation>"}}
"""

RELEVANCY_PROMPT = """\
You are an expert evaluator. Assess how well the **Generated Answer** \
addresses the **Query**.

Rate the relevance on a scale from 0.0 (completely irrelevant) to 1.0 \
(directly and fully relevant).

Consider:
- Does the answer address the core question?
- Is the answer on-topic, or does it drift to unrelated content?
- Would the answer satisfy someone asking this question?

**Query:** {query}
**Generated Answer:** {answer}

Respond with ONLY valid JSON (no markdown, no commentary):
{{"score": <float 0.0-1.0>, "reasoning": "<brief explanation>"}}
"""

COMPLETENESS_PROMPT = """\
You are an expert evaluator. Assess how completely the **Generated Answer** \
covers all aspects of the **Query**.

Rate the completeness on a scale from 0.0 (misses everything) to 1.0 \
(covers all aspects thoroughly).

Consider:
- Does the query have multiple aspects or sub-questions?
- Does the answer address each aspect?
- Are there obvious omissions that a complete answer would include?

**Query:** {query}
**Generated Answer:** {answer}

Respond with ONLY valid JSON (no markdown, no commentary):
{{"score": <float 0.0-1.0>, "reasoning": "<brief explanation>"}}
"""

TOOL_CALL_ACCURACY_PROMPT = """\
You are an expert evaluator. Assess whether the **Tool Calls** made by the \
agent were appropriate and correct for the given **Query**.

Rate tool call accuracy on a scale from 0.0 (completely wrong tools/calls) \
to 1.0 (all tools used correctly and appropriately).

Consider:
- Were the right tools selected for the task?
- Were the tool arguments correct and complete?
- Were any necessary tool calls missing?
- Were any unnecessary tool calls made?

**Query:** {query}
**Tool Calls:** {tool_calls}
**Generated Answer:** {answer}

Respond with ONLY valid JSON (no markdown, no commentary):
{{"score": <float 0.0-1.0>, "reasoning": "<brief explanation>"}}
"""

TASK_COMPLETION_PROMPT = """\
You are an expert evaluator. Assess whether the agent successfully **completed** \
the task described in the **Query**.

Rate task completion on a scale from 0.0 (task not completed at all) to 1.0 \
(task fully completed successfully).

Consider:
- Did the agent achieve the goal stated in the query?
- Is the final answer a complete solution to the task?
- Are there any remaining steps or unresolved parts?
- Would the user be satisfied with this result?

**Query:** {query}
**Generated Answer:** {answer}

Respond with ONLY valid JSON (no markdown, no commentary):
{{"score": <float 0.0-1.0>, "reasoning": "<brief explanation>"}}
"""
