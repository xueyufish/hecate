"""S11 — independent review chain: the full acceptance sample of plan step1.

Manifest: S11 (P06, P07). Composes the complete chain "read material -> draft
summary -> independent review -> human approval -> ticket write" that the plan
lists as the business-system-free acceptance sample:

- the draft agent reads the synthetic corpus through a real ToolRegistry
  boundary stub (``read_corpus_doc``) and produces the summary;
- a second, distinct reviewer agent receives ONLY the draft text plus review
  instructions (own agent row, own session — not the draft conversation);
- review rejection proposes no ticket: zero side effects, zero approval events;
- review pass routes the write through the ask rule; after the scripted human
  approval the ticket service executes exactly once.

Assertions are deterministic (read counts, captured reviewer input, agent
identity, approval event order, side-effect counts). Content-quality review
belongs to the Tier-2/step10 rubric scope and is deliberately out of scope.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.tool import ToolModel
from hecate.runtime.eventstore import EventType
from hecate.tools.tool.registry import ToolRegistry
from tests.scenarios.conftest import (
    SCENARIO_MODEL,
    SCENARIO_TOOL,
    TICKET_TOOL_PARAMETERS,
    agent_chat_url,
    chat_body,
    llm_response,
    tool_call,
)

CORPUS_TOOL = "read_corpus_doc"
CORPUS_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {"query": {"type": "string", "description": "Corpus keyword query"}},
    "required": ["query"],
}
CORPUS_QUERY = "reimbursement limit"
DRAFT_MARKER = "DRAFT-SUMMARY:"
REVIEW_MARKER = "INDEPENDENT-REVIEW-REQUEST"
TICKET_ARGS = {"title": "Reviewed expense summary", "body": "Standard limit 800 credits per proc-reimburse v2.1."}
DRAFT_PROMPT = "Summarize the reimbursement procedure for filing."
# Never appears in the reviewer's input: proves the reviewer does not inherit
# the draft conversation, only the draft text.
DRAFT_PROMPT_PHRASE = "Summarize the reimbursement procedure"


@pytest_asyncio.fixture
async def corpus_read_service(
    ticket_service,
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    default_workspace,
):
    """Bind a corpus-read stub tool at the ToolRegistry boundary.

    Depends on ``ticket_service`` so the patch composes: corpus calls are
    served here, everything else falls through to the ticket wrapper (ticket
    stub, then the real registry). Records every corpus read for side-effect
    counting.
    """
    from tests.scenarios.tools.corpus_index import CorpusIndex

    index = CorpusIndex()
    calls: list[dict[str, Any]] = []
    original_execute = ToolRegistry.execute

    async def patched_execute(self: ToolRegistry, name: str, args: dict[str, Any], context: dict | None = None):
        if name == CORPUS_TOOL:
            calls.append({"args": dict(args or {}), "context": dict(context or {})})
            hits = index.search((args or {}).get("query", ""), "employee")
            if not hits:
                return {"error": "no corpus hit", "doc_id": None}
            top = hits[0]
            return {"doc_id": top.doc_id, "version": top.version, "anchor": top.anchor, "text": top.text}
        return await original_execute(self, name, args, context)

    monkeypatch.setattr(ToolRegistry, "execute", patched_execute)

    tool = ToolModel(
        workspace_id=default_workspace.id,
        name=CORPUS_TOOL,
        description="Read a document from the synthetic scenario corpus",
        source="custom",
        parameters=CORPUS_TOOL_PARAMETERS,
    )
    db_session.add(tool)
    await db_session.flush()

    class _Recorder:
        call_count = property(lambda self: len(calls))
        calls = staticmethod(lambda: calls)

    return _Recorder()


@pytest_asyncio.fixture
async def draft_agent(db_session: AsyncSession, default_workspace) -> AgentModel:
    """Drafting agent: can read the corpus, cannot write tickets."""
    agent = AgentModel(
        workspace_id=default_workspace.id,
        name="Draft Agent",
        model_config_db={"model": SCENARIO_MODEL},
        mode="chat",
        tools=[CORPUS_TOOL],
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


@pytest_asyncio.fixture
async def reviewer_agent(db_session: AsyncSession, default_workspace) -> AgentModel:
    """Independent reviewer: can file tickets (approval-gated), cannot read the corpus."""
    tool = ToolModel(
        workspace_id=default_workspace.id,
        name=SCENARIO_TOOL,
        description="Create a ticket in the test ticket service",
        source="custom",
        parameters=TICKET_TOOL_PARAMETERS,
    )
    agent = AgentModel(
        workspace_id=default_workspace.id,
        name="Independent Reviewer",
        model_config_db={"model": SCENARIO_MODEL},
        mode="chat",
        tools=[SCENARIO_TOOL],
    )
    db_session.add_all([tool, agent])
    await db_session.flush()
    return agent


def _make_responder(review_mode: str, reviewer_seen: list[str]):
    """Script both agents' loops.

    Phase detection is content-based, never call-order-based: a corpus tool
    result continues the draft loop, a ticket tool result finalizes the review
    loop, and the review phase is keyed on the review-request marker.
    """

    async def responder(messages: list[dict[str, Any]]) -> Any:
        last = messages[-1]
        content = str(last.get("content") or "")
        if last.get("role") == "tool":
            if '"ticket_id"' in content or "'ticket_id'" in content:
                return llm_response(content="Review passed and filed as ticket TKT-0000 after independent review.")
            if '"doc_id"' in content or "'doc_id'" in content:
                return llm_response(
                    content=(
                        f"{DRAFT_MARKER} Standard per-claim reimbursement limit is 800 credits; "
                        "claims above 5000 credits escalate to the finance committee."
                    )
                )
            raise AssertionError(f"unexpected tool result in scripted loop: {content[:120]}")

        joined = " ".join(str(m.get("content") or "") for m in messages)
        if REVIEW_MARKER in joined:
            reviewer_seen.append(joined)
            if review_mode == "reject":
                return llm_response(content="REVIEW-REJECTED: draft is not backed by the cited procedure version.")
            return llm_response(tool_calls=[tool_call("call_s11_review_1", SCENARIO_TOOL, TICKET_ARGS)])

        return llm_response(tool_calls=[tool_call("call_s11_draft_1", CORPUS_TOOL, {"query": CORPUS_QUERY})])

    return responder


async def test_s11_independent_review_chain_executes_once(
    scenario_client,
    ticket_service,
    scripted_llm,
    add_policy_rule,
    grant_approvals,
    scenario_event_store,
    corpus_read_service,
    draft_agent,
    reviewer_agent,
) -> None:
    await add_policy_rule(action="ask")
    reviewer_seen: list[str] = []
    scripted_llm(_make_responder("approve", reviewer_seen))

    draft_session = uuid.uuid4()
    review_session = uuid.uuid4()

    draft_response = await scenario_client.post(
        agent_chat_url(draft_agent),
        json=chat_body(DRAFT_PROMPT, session_id=draft_session),
    )
    assert draft_response.status_code == 200
    draft = draft_response.json()["choices"][0]["message"]["content"]
    assert DRAFT_MARKER in draft, "the draft turn must produce the scripted draft summary"

    assert corpus_read_service.call_count == 1, "material must be read exactly once, via the tool boundary"
    assert corpus_read_service.calls()[0]["context"].get("workspace_id") == str(draft_agent.workspace_id)

    review_response = await scenario_client.post(
        agent_chat_url(reviewer_agent),
        json=chat_body(
            f"{REVIEW_MARKER}\nReview the following draft and file it if it passes:\n\n{draft}",
            session_id=review_session,
        ),
    )
    assert review_response.status_code == 200
    final = review_response.json()["choices"][0]["message"]["content"]
    assert "TKT-0000" in final, "final answer must reference the real business result"

    # Independence: the reviewer consumed the actual draft, but never the
    # draft conversation's own prompt; the agents are distinct rows.
    assert len(reviewer_seen) == 1
    assert DRAFT_MARKER in reviewer_seen[0], "reviewer input must contain the draft text"
    assert DRAFT_PROMPT_PHRASE not in reviewer_seen[0], "reviewer must not inherit the draft conversation"
    assert draft_agent.id != reviewer_agent.id

    # Approval gating: exactly one asked/decided pair, approved, in the review
    # session only.
    review_events = await scenario_event_store.get_events(session_id=review_session, from_version=0)
    asked = [e for e in review_events if e.event_type == EventType.APPROVAL_ASKED]
    decided = [e for e in review_events if e.event_type == EventType.APPROVAL_DECIDED]
    assert len(asked) == 1 and len(decided) == 1
    assert decided[0].payload["approved"] is True

    draft_events = await scenario_event_store.get_events(session_id=draft_session, from_version=0)
    approval_events = [
        e for e in draft_events if e.event_type in (EventType.APPROVAL_ASKED, EventType.APPROVAL_DECIDED)
    ]
    assert not approval_events, "the draft turn must not touch the approval path"

    assert ticket_service.side_effect_count == 1, "approved chain writes exactly one ticket"
    assert ticket_service.calls[0].context.get("workspace_id") == str(reviewer_agent.workspace_id)


async def test_s11_review_rejection_blocks_write(
    scenario_client,
    ticket_service,
    scripted_llm,
    add_policy_rule,
    scenario_event_store,
    corpus_read_service,
    draft_agent,
    reviewer_agent,
) -> None:
    await add_policy_rule(action="ask")
    reviewer_seen: list[str] = []
    scripted_llm(_make_responder("reject", reviewer_seen))

    draft_session = uuid.uuid4()
    review_session = uuid.uuid4()

    draft_response = await scenario_client.post(
        agent_chat_url(draft_agent),
        json=chat_body(DRAFT_PROMPT, session_id=draft_session),
    )
    assert draft_response.status_code == 200

    review_response = await scenario_client.post(
        agent_chat_url(reviewer_agent),
        json=chat_body(
            f"{REVIEW_MARKER}\nReview the following draft and file it if it passes:\n\n"
            f"{draft_response.json()['choices'][0]['message']['content']}",
            session_id=review_session,
        ),
    )
    assert review_response.status_code == 200
    assert "REVIEW-REJECTED" in review_response.json()["choices"][0]["message"]["content"]

    assert ticket_service.side_effect_count == 0, "a rejected review must not produce the protected write"

    review_events = await scenario_event_store.get_events(session_id=review_session, from_version=0)
    approval_events = [
        e for e in review_events if e.event_type in (EventType.APPROVAL_ASKED, EventType.APPROVAL_DECIDED)
    ]
    assert not approval_events, "review rejection must not even reach the approval path"

    assert corpus_read_service.call_count == 1
    assert DRAFT_MARKER in reviewer_seen[0]
