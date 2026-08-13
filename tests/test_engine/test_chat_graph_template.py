"""Tests for the chat graph template (build_chat_graph).

Verifies node count, edge topology, channel definitions, and entry point
for both the basic and suggestion-enabled variants.
"""

from __future__ import annotations

from hecate.engine.templates import build_chat_graph
from hecate.engine.types import ChannelType, NodeType


class TestBuildChatGraphBasic:
    """Verify the basic chat graph without suggestions."""

    def test_node_count(self):
        """Basic chat graph has 3 nodes: llm, check_tools, tool_call."""
        graph = build_chat_graph(model="gpt-4o")
        assert len(graph.nodes) == 3
        assert "llm" in graph.nodes
        assert "check_tools" in graph.nodes
        assert "tool_call" in graph.nodes

    def test_node_types(self):
        """Nodes have correct types."""
        graph = build_chat_graph(model="gpt-4o")
        assert graph.nodes["llm"].type == NodeType.CONVERSATION
        assert graph.nodes["check_tools"].type == NodeType.CONDITION
        assert graph.nodes["tool_call"].type == NodeType.TOOL_CALL

    def test_entry_point(self):
        """Entry point is 'llm'."""
        graph = build_chat_graph(model="gpt-4o")
        assert graph.entry == "llm"

    def test_edge_topology(self):
        """Edges form: llm → check_tools → {true: tool_call, false: __end__}, tool_call → llm."""
        graph = build_chat_graph(model="gpt-4o")
        assert len(graph.edges) == 3

        # llm → check_tools
        e0 = graph.edges[0]
        assert e0.source == "llm"
        assert e0.target == "check_tools"

        # check_tools → conditional
        e1 = graph.edges[1]
        assert e1.source == "check_tools"
        assert isinstance(e1.target, dict)
        assert e1.target["true"] == "tool_call"
        assert e1.target["false"] == "__end__"

        # tool_call → llm (cycle)
        e2 = graph.edges[2]
        assert e2.source == "tool_call"
        assert e2.target == "llm"

    def test_channel_definitions(self):
        """Required channels are defined with correct types."""
        graph = build_chat_graph(model="gpt-4o")
        assert "messages" in graph.state
        assert graph.state["messages"].type == ChannelType.TOPIC
        assert "_has_tool_call" in graph.state
        assert graph.state["_has_tool_call"].type == ChannelType.LAST_VALUE
        assert "_route" in graph.state
        assert "_session_id" in graph.state
        assert "_agent_id" in graph.state
        assert "_user_id" in graph.state
        assert "_turn_index" in graph.state

    def test_graph_name(self):
        """Graph name is 'chat-agent'."""
        graph = build_chat_graph(model="gpt-4o")
        assert graph.name == "chat-agent"

    def test_model_propagated_to_llm_node(self):
        """Model parameter is stored in llm node config."""
        graph = build_chat_graph(model="gpt-4o-mini")
        assert graph.nodes["llm"].config["model"] == "gpt-4o-mini"


class TestBuildChatGraphWithSuggestions:
    """Verify the chat graph with suggestions enabled."""

    def test_node_count_with_suggestions(self):
        """Chat graph with suggestions has 4 nodes."""
        graph = build_chat_graph(model="gpt-4o", enable_suggestions=True)
        assert len(graph.nodes) == 4
        assert "suggestions" in graph.nodes
        assert graph.nodes["suggestions"].type == NodeType.SUGGESTION

    def test_suggestion_routing(self):
        """False branch from check_tools routes to suggestions, not __end__."""
        graph = build_chat_graph(model="gpt-4o", enable_suggestions=True)
        # check_tools → conditional
        e1 = graph.edges[1]
        assert e1.target["false"] == "suggestions"

    def test_suggestions_to_end(self):
        """Suggestion node has an edge to __end__."""
        graph = build_chat_graph(model="gpt-4o", enable_suggestions=True)
        suggestion_edges = [e for e in graph.edges if e.source == "suggestions"]
        assert len(suggestion_edges) == 1
        assert suggestion_edges[0].target == "__end__"

    def test_generate_opening_in_config(self):
        """generate_opening flag is stored in suggestion node config."""
        graph = build_chat_graph(model="gpt-4o", enable_suggestions=True, generate_opening=True)
        assert graph.nodes["suggestions"].config["generate_opening"] is True


class TestBuildChatGraphTools:
    """Verify tools injection into the LLM node config."""

    def test_tools_injected_into_llm_node(self):
        """When tools is provided, the llm node config includes the 'tools' key."""
        tools = [
            {"type": "function", "function": {"name": "web_search", "description": "search"}},
            {"type": "function", "function": {"name": "calc", "description": "calculate"}},
        ]
        graph = build_chat_graph(model="gpt-4o", tools=tools)
        assert graph.nodes["llm"].config["tools"] == tools

    def test_tools_omitted_when_none(self):
        """When tools is None, the llm node config does not contain the 'tools' key."""
        graph = build_chat_graph(model="gpt-4o")
        assert "tools" not in graph.nodes["llm"].config

    def test_tools_empty_list_preserved(self):
        """An empty tools list is stored as an empty list (not omitted)."""
        graph = build_chat_graph(model="gpt-4o", tools=[])
        assert graph.nodes["llm"].config["tools"] == []

    def test_tools_with_suggestions(self):
        """Tools injection coexists with the suggestions node."""
        tools = [{"type": "function", "function": {"name": "x"}}]
        graph = build_chat_graph(model="gpt-4o", enable_suggestions=True, tools=tools)
        assert graph.nodes["llm"].config["tools"] == tools
        assert "suggestions" in graph.nodes


class TestChatGraphToolLoopE2E:
    """End-to-end chat graph tool-calling loop test."""

    def _scripted_llm_port(self, scripts):
        from collections.abc import AsyncGenerator

        from hecate.engine.ports import EnginePort

        class _ScriptedPort(EnginePort):
            def __init__(self, scripts: list[list[dict]]) -> None:
                self._scripts = list(scripts)
                self._idx = 0
                self.llm_invoke_calls = 0

            async def llm_invoke_structured(self, messages, config) -> AsyncGenerator[dict, None]:
                idx = self._idx
                self._idx += 1
                if idx < len(self._scripts):
                    for chunk in self._scripts[idx]:
                        yield chunk

            async def llm_invoke(self, messages, config) -> AsyncGenerator[str, None]:
                self.llm_invoke_calls += 1
                return
                yield ""

            async def tool_execute(self, name, args, context=None):
                return None

            async def knowledge_query(self, query, kb_ids):
                return []

            async def checkpoint_save(self, state):
                import uuid

                return uuid.uuid4()

            async def checkpoint_load(self, checkpoint_id):
                return {}

            async def conversation_load(self, session_id):
                return []

            async def conversation_save(self, session_id, messages):
                return None

            async def create_span(self, name, parent_id=None, attributes=None):
                return None

            async def end_span(self, span_id, output_data=None, usage=None):
                return None

        return _ScriptedPort(scripts)

    async def test_tool_loop_runs_until_no_tool_call(self) -> None:
        import uuid as _uuid

        from hecate.engine.checkpoint import InMemoryCheckpointStore
        from hecate.engine.compiler import GraphCompiler
        from hecate.engine.pregel import PregelRuntime
        from hecate.engine.types import StreamMode, WorkerResult
        from hecate.engine.worker import Worker
        from hecate.engine.workers.condition_worker import ConditionWorker
        from hecate.engine.workers.llm_worker import LLMWorker

        class _StubToolCallWorker(Worker):
            async def execute(self, node_id, node_config, channel_snapshot, execution_context=None):
                messages = channel_snapshot.get("messages", [])
                for msg in reversed(messages):
                    if msg.get("role") == "assistant" and msg.get("tool_calls"):
                        tool_calls = msg["tool_calls"]
                        results = []
                        for tc in tool_calls:
                            func = tc.get("function", {})
                            name = func.get("name", "?")
                            results.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc.get("id", ""),
                                    "name": name,
                                    "content": f"mock result for {name}",
                                }
                            )
                        return WorkerResult(node_id=node_id, channel_updates={"messages": results})
                return WorkerResult(node_id=node_id, channel_updates={"messages": []})

        class _DispatchWorker(Worker):
            def __init__(self, port):
                super().__init__()
                self._llm = LLMWorker(port=port)
                self._cond = ConditionWorker()
                self._tool = _StubToolCallWorker()
                self._llm_call_count = 0

            async def execute(self, node_id, node_config, channel_snapshot, execution_context=None):
                if node_id == "llm":
                    self._llm_call_count += 1
                    return await self._llm.execute(node_id, node_config, channel_snapshot, execution_context)
                if node_id == "check_tools":
                    return await self._cond.execute(node_id, node_config, channel_snapshot, execution_context)
                if node_id == "tool_call":
                    return await self._tool.execute(node_id, node_config, channel_snapshot, execution_context)
                return WorkerResult(node_id=node_id, channel_updates={})

        tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "web_search", "arguments": '{"query":"weather"}'},
            },
        ]
        scripts = [
            [
                {"content": "Let me check.", "tool_calls": None},
                {"content": None, "tool_calls": tool_calls},
            ],
            [
                {"content": "It's ", "tool_calls": None},
                {"content": "sunny.", "tool_calls": None},
                {"content": None, "tool_calls": None},
            ],
        ]
        port = self._scripted_llm_port(scripts)

        graph = build_chat_graph(
            model="gpt-4o",
            tools=[{"type": "function", "function": {"name": "web_search"}}],
        )
        compiled = GraphCompiler().compile(graph)
        runtime = PregelRuntime(
            graph=compiled,
            worker=_DispatchWorker(port),
            checkpoint_store=InMemoryCheckpointStore(),
        )

        results: list = []
        async for event in runtime.execute(
            session_id=_uuid.uuid4(),
            initial_input={
                "messages": [{"role": "user", "content": "weather?"}],
                "_session_id": "s",
                "_agent_id": "a",
                "_user_id": "u",
                "_turn_index": 0,
            },
            stream_mode=StreamMode.VALUES,
            execution_mode="conversational",
        ):
            results.append(event)

        assert port._idx == 2
        final_state = results[-1]["state"] if results else {}
        messages = final_state.get("messages", [])
        assistant_with_tool = [m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")]
        tool_results = [m for m in messages if m.get("role") == "tool"]
        final_assistant_text = [m for m in messages if m.get("role") == "assistant" and not m.get("tool_calls")]
        assert len(assistant_with_tool) >= 1
        assert len(tool_results) >= 1
        assert tool_results[0]["content"] == "mock result for web_search"
        assert final_assistant_text[-1]["content"] == "It's sunny."
        assert "_has_tool_call" in final_state
        assert final_state["_has_tool_call"] is False
