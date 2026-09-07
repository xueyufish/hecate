from __future__ import annotations

from hecate.runtime.task_phase import TaskPhase, detect_task_phase


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _tool_result(content: str) -> dict:
    return {"role": "tool", "content": content}


class TestDetectTaskPhase:
    def test_empty_conversation_defaults_to_explore(self) -> None:
        assert detect_task_phase([]) is TaskPhase.EXPLORE
        assert detect_task_phase([_user("hi")]) is TaskPhase.EXPLORE

    def test_explore_keywords(self) -> None:
        assert detect_task_phase([_user("Research how does the engine handle retries")]) is TaskPhase.EXPLORE

    def test_converge_keywords(self) -> None:
        assert detect_task_phase([_user("We compared the options earlier; please recommend one")]) is TaskPhase.CONVERGE

    def test_execute_keywords(self) -> None:
        assert detect_task_phase([_user("Implement the fix and update the tests")]) is TaskPhase.EXECUTE

    def test_verify_keywords(self) -> None:
        assert detect_task_phase([_user("Verify the change passes the tests")]) is TaskPhase.VERIFY

    def test_failed_tool_result_forces_verify(self) -> None:
        messages = [
            _user("write the file"),
            _tool_result("Error: disk full"),
        ]
        assert detect_task_phase(messages) is TaskPhase.VERIFY

    def test_channel_snapshot_carried_phase_wins(self) -> None:
        messages = [_user("implement it now")]
        assert detect_task_phase(messages, {"_task_phase": "verify"}) is TaskPhase.VERIFY

    def test_channel_snapshot_invalid_phase_ignored(self) -> None:
        assert detect_task_phase([_user("implement it now")], {"_task_phase": "nonsense"}) is TaskPhase.EXECUTE

    def test_user_intent_outranks_assistant_text(self) -> None:
        messages = [
            {"role": "assistant", "content": "I will verify check test everything"},
            _user("go implement build run it"),
        ]
        assert detect_task_phase(messages) is TaskPhase.EXECUTE

    def test_phase_is_string_enum(self) -> None:
        assert TaskPhase.VERIFY.value == "verify"
        assert detect_task_phase([_user("verify it")]).value == "verify"
