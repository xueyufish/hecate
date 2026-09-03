"""Evaluation framework for measuring RAG and Agent quality.

Provides the core abstractions for building evaluation pipelines:

- **Score** — structured metric result (name, value, reasoning, source)
- **EvalInput / EvalOutput** — typed I/O for evaluator execution
- **Evaluator** — abstract base class that all evaluators implement
- **EvaluationEngine** — orchestrates batch evaluation runs
- **Built-in evaluators** — RAG (Ragas-backed) and Agent (LLM-as-Judge)
"""
