# EDU-C2-014 — Proof-of-Boundary: the output boundary
#
# The hard boundary: a gate-failed answer is NEVER released. This file drives
# the boundary through the real compiled agent rather than a single node, so
# it proves the property the caller actually experiences — the answer is
# withheld, and the structured program list is withheld with it.
#
# The screen runs at three layers on three representations of the same answer
# (inner pipeline gate, outer backbone gate, and the agent's own output
# envelope). Each is exercised here.
#
# Blocking is only half of the boundary. The base envelope resolves the
# released text as `formatted_output or result`, so the gate has to CONTAIN as
# well as refuse: an empty formatted_output is falsy and re-activates the
# fallback to the un-screened answer. TestBlockedAnswerIsContained pins that
# distinction directly, because an empty string reads like a fix and is not.

import json

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import CorporateTrainingFAQAgent, TrainingFAQGraphNode
from src.nodes.output_validate import screen_text, screen_value
from src.nodes.post_process_node import _OUTPUT_BEARING_FIELDS, _WITHHELD_NOTICE, PostProcessNode

_QUESTION = "which leadership programs can a team lead with three years of service apply for?"

# A direct personal identifier, not a credential: the framework's own output
# scan looks for credential shapes, so a credential here would be stopped
# before this template's gate is reached and the test would prove nothing
# about this template.
_LEAK = "123-45-6789"
_LEAKING_ANSWER = f"Programme TRN-LEAD-101 starts 2026-03-31.\nProgramme owner national ID on file: {_LEAK}."


def _invoke(question=_QUESTION, input_context=None):
    ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL, caller_id="pb-output-gate")
    return CorporateTrainingFAQAgent().invoke(question, ctx=ctx, input_context=input_context or {})


def _merged_state(answer: str) -> dict:
    """The outer state as the main slot leaves it, via the real merge_output.

    Built through TrainingFAQGraphNode.merge_output rather than hand-written so
    the fixture cannot drift away from the mapping it is meant to represent.
    """
    sub_result = {
        "answer": answer,
        "eligibility_result": "yes",
        "application_procedure": "Submit the leadership nomination form through the learning portal.",
        "deadline_info": "Applications close six weeks before each quarterly cohort start.",
        "hr_escalation": False,
        "out_of_scope": False,
        "programs": json.dumps([{"id": "TRN-LEAD-101", "program_name": "Leadership Foundations"}]),
        "status": AgentStatus.SUCCESS.value,
    }
    state = dict(TrainingFAQGraphNode().merge_output({}, sub_result))
    state["answer"] = answer
    return state


def _through_the_gate(answer: str) -> tuple[dict, dict]:
    """Run the outer gate over *answer* and return (post-gate state, envelope).

    The node's partial dict is applied onto the state the way the graph runtime
    applies it, then the agent's real get_output() shapes the envelope — so the
    assertions below read the value a caller would actually receive.
    """
    state = _merged_state(answer)
    state.update(PostProcessNode().execute(state))
    return state, CorporateTrainingFAQAgent().get_output(state)


class TestCleanAnswerIsReleased:
    def test_catalog_answer_reaches_the_caller_with_its_programs(self):
        result = _invoke()
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["output"]
        assert result["programs"], "a grounded answer must surface the programs it rests on"

    def test_rendered_identifiers_are_byte_identical(self):
        """The screen must not mangle the identifiers this domain renders."""
        output = _invoke()["output"]
        assert "TRN-LEAD-101" in output


class TestBlockedAnswerIsWithheld:
    @pytest.mark.parametrize(
        "leak",
        [
            "Employee national ID on file: 123-45-6789.",
            "Contact alex.morgan@example.com to enrol.",
            "Use key sk-ABCDEFGHIJKLMNOP0123456789 for the catalog API.",
            "Staff record 1234-5678-9012 is enrolled.",
        ],
    )
    def test_outer_gate_drops_a_leaking_answer(self, leak):
        """PostProcessNode is the last stop before the answer becomes `output`:
        a violation there must replace the surfaced text, not pass it on."""
        result = PostProcessNode().execute({"result": f"Catalog answer.\n{leak}"})
        assert result["status"] == AgentStatus.ERROR.value
        assert result["formatted_output"] == _WITHHELD_NOTICE
        assert leak not in result["formatted_output"]
        assert leak not in " ".join(result["error_log"])

    def test_structured_programs_are_withheld_when_they_fail_the_screen(self):
        """get_output() re-screens the field it adds on top of the envelope —
        a leak there withholds the list and flips the status, never a partial
        payload."""
        agent = CorporateTrainingFAQAgent()
        state = {
            "status": AgentStatus.SUCCESS.value,
            "formatted_output": "Catalog answer.",
            "programs": '[{"id": "TRN-LEAD-101", "source": "mail alex.morgan@example.com"}]',
        }
        envelope = agent.get_output(state)
        assert envelope["status"] == AgentStatus.ERROR.value
        assert "programs" not in envelope

    def test_non_success_run_never_carries_programs(self):
        agent = CorporateTrainingFAQAgent()
        envelope = agent.get_output({"status": AgentStatus.ERROR.value, "programs": '[{"id": "TRN-LEAD-101"}]'})
        assert "programs" not in envelope


class TestBlockedAnswerIsContained:
    """Refusing is not containing.

    By the time the outer gate runs, the main slot has already mapped the
    rendered answer into `result`, and the base envelope reads
    `formatted_output or result`. So an ERROR status with an emptied
    formatted_output still SHIPS the answer — the empty string is falsy, and
    the fallback it re-opens leads straight back to the text that was just
    refused.
    """

    def test_error_envelope_carries_no_released_text(self):
        state, envelope = _through_the_gate(_LEAKING_ANSWER)
        assert envelope["status"] == AgentStatus.ERROR.value
        assert _LEAK not in (envelope["output"] or "")
        assert "TRN-LEAD-101" not in (envelope["output"] or "")
        assert envelope["output"] == _WITHHELD_NOTICE
        assert "programs" not in envelope

    def test_an_empty_formatted_output_would_have_released_the_answer(self):
        """The falsy/truthy distinction, pinned against the real envelope.

        This is the whole defect in one assertion: `""` looks like a cleared
        field, but `"" or result` is `result`. Only a non-empty placeholder
        short-circuits the fallback, so the withheld notice must stay truthy —
        a future "tidy-up" that empties it silently re-opens the leak.
        """
        agent = CorporateTrainingFAQAgent()
        released = _merged_state(_LEAKING_ANSWER)

        # What an empty clear actually does, measured rather than assumed.
        empty_clear = dict(released)
        empty_clear.update({"formatted_output": "", "status": AgentStatus.ERROR.value})
        assert agent.get_output(empty_clear)["output"] == _LEAKING_ANSWER

        # What this node ships instead.
        assert bool(_WITHHELD_NOTICE) is True
        assert screen_text(_WITHHELD_NOTICE) is None
        _, envelope = _through_the_gate(_LEAKING_ANSWER)
        assert envelope["output"] != _LEAKING_ANSWER

    def test_every_answer_bearing_field_is_cleared(self):
        state, _ = _through_the_gate(_LEAKING_ANSWER)
        for field in _OUTPUT_BEARING_FIELDS:
            assert state[field] is None, f"{field} still holds answer content after the gate blocked"

    def test_clearing_covers_everything_the_main_slot_maps(self):
        """Inventory guard: a field added to merge_output() must be either
        cleared by the gate or declared content-free here, never neither."""
        # error_code is a closed-set reason code, never caller content, so the
        # output gate has nothing to clear in it.
        content_free = {"status", "hr_escalation", "out_of_scope", "error_code"}
        mapped = set(TrainingFAQGraphNode().merge_output({}, {}))
        unaccounted = mapped - set(_OUTPUT_BEARING_FIELDS) - content_free
        assert not unaccounted, f"merge_output maps {sorted(unaccounted)}; the gate neither clears nor exempts it"

    def test_error_envelope_leaks_no_diagnostics(self):
        """The refusal names the field that failed — not the matched text, not
        the blocked form, and nothing about the machine it ran on."""
        state, envelope = _through_the_gate(_LEAKING_ANSWER)
        surfaced = json.dumps(envelope, default=str) + " ".join(state.get("error_log") or [])
        assert _LEAK not in surfaced
        assert "national_id" not in surfaced
        assert "Traceback" not in surfaced
        assert ".py" not in surfaced
        assert "/Users/" not in surfaced and "/builds/" not in surfaced


class TestContainmentEndToEnd:
    """The same property through the real compiled agent and a real invoke().

    The outer gate exists to catch an answer the inner gate did not, so the
    fixture blinds the inner screen and lets the outer one fire for real. The
    two screens are separate module references, so blinding
    src.nodes.output_validate leaves PostProcessNode's own screen live.
    """

    _CLEAN_MARKER = "Leadership Foundations"

    @pytest.fixture
    def catalog(self, tmp_path, monkeypatch):
        """Serve the agent a catalog from tmp_path, with the inner gate blind."""

        def _serve(leaking: bool):
            records = json.loads((self._kb_source()).read_text(encoding="utf-8"))
            if leaking:
                records[0]["content"] += f" Programme owner national ID on file: {_LEAK}."
            kb = tmp_path / "catalog.json"
            kb.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

            from src.graph import graph as graph_module
            from src.nodes import output_validate

            monkeypatch.setattr(output_validate, "screen_text", lambda text: None)
            monkeypatch.setattr(output_validate, "screen_value", lambda value: None)
            monkeypatch.setattr(
                graph_module,
                "runtime_config",
                lambda: {
                    "max_retry": 3,
                    "timeout_s": 30,
                    "retrieval": {"top_k": 1, "score_threshold": 0.25, "kb_path": str(kb)},
                    "answer": {
                        "max_answer_chars": 8000,
                        "supported_languages": ["ja", "vi", "en"],
                        "escalation_keywords": ["grievance"],
                    },
                },
            )

        return _serve

    @staticmethod
    def _kb_source():
        from pathlib import Path

        from src.graph.graph import runtime_config

        return Path(runtime_config()["retrieval"]["kb_path"])

    def test_blocked_answer_never_reaches_the_caller(self, catalog):
        catalog(leaking=True)
        envelope = _invoke()
        assert envelope["status"] == AgentStatus.ERROR.value
        assert "PostProcessNode" in envelope["node_history"], "the outer gate must actually have run"
        assert _LEAK not in (envelope["output"] or "")
        assert self._CLEAN_MARKER not in (envelope["output"] or "")
        assert envelope["output"] == _WITHHELD_NOTICE
        assert "programs" not in envelope

    def test_clean_answer_still_reaches_the_caller_through_the_same_fixture(self, catalog):
        """Control: the fixture itself withholds nothing. Same tmp catalog,
        same blinded inner screen — only the leak removed — and the answer is
        released in full."""
        catalog(leaking=False)
        envelope = _invoke()
        assert envelope["status"] == AgentStatus.SUCCESS.value
        assert self._CLEAN_MARKER in envelope["output"]
        assert envelope["output"] != _WITHHELD_NOTICE
        assert envelope["programs"]


class TestScreenIsOneImplementation:
    """Three layers, one rule — a leak cannot travel out through whichever
    representation happens not to be prose."""

    def test_prose_and_structure_agree(self):
        leak = "123-45-6789"
        assert screen_text(f"id {leak}") == "national_id"
        assert screen_value([{"note": f"id {leak}"}]) == "national_id"

    def test_clean_prose_and_structure_agree(self):
        assert screen_text("TRN-LEAD-101 cohort starts 2026-03-31") is None
        assert screen_value([{"id": "TRN-LEAD-101", "deadline": "2026-03-31"}]) is None
