# EDU-C2-014 — Proof-of-Boundary: the escalation flag is advisory
#
# The boundary: raising the escalation flag routes a case to the training team
# — it must never suppress the answer. An advisory signal that silently
# swallowed the response would be indistinguishable, from the caller's side,
# from a blocked output, and callers would lose the catalog answer they are
# entitled to.
#
# The complementary boundary is that the flag is not raised by accident: an
# ordinary catalog answer leaves it False.

from framework.schemas.agent_status import AgentStatus
from src.nodes.output_validate import OutputValidateNode
from src.schemas.state import to_json

_ANSWER = (
    "# Corporate Training & Certification — Catalog Answer\n\n"
    "**Eligibility**: Conditionally eligible\n"
    "**How to apply**: Submit the nomination form through the learning portal.\n"
)
_CONFIG = {"escalation_keywords": ["grievance", "waiver", "exception", "appeal"]}


def _run(answer):
    state = {"answer": answer, "programs": to_json([]), "answer_config": to_json(_CONFIG)}
    return OutputValidateNode().execute(state)


class TestEscalationIsAdvisory:
    def test_matched_keyword_raises_the_flag(self):
        result = _run(f"{_ANSWER}\nRequest a waiver from the training team if the tenure rule blocks you.")
        assert result["hr_escalation"] is True

    def test_the_answer_is_still_delivered(self):
        """The load-bearing assertion: an advisory flag must not block."""
        result = _run(f"{_ANSWER}\nFile a grievance if you disagree with the decision.")
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["is_valid_output"] is True
        assert "error_message" not in result

    def test_a_routine_answer_does_not_raise_the_flag(self):
        result = _run(_ANSWER)
        assert result["hr_escalation"] is False
        assert result["status"] == AgentStatus.SUCCESS.value

    def test_an_unconfigured_keyword_list_never_raises_the_flag(self):
        state = {"answer": f"{_ANSWER}\nFile a grievance.", "programs": to_json([])}
        assert OutputValidateNode().execute(state)["hr_escalation"] is False
