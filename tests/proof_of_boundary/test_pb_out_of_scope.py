# EDU-C2-014 — Proof-of-Boundary: no coverage means no invention
#
# The boundary this file guards: when the catalog holds nothing relevant, the
# agent says so. It must NOT fabricate a program, an eligibility verdict, a
# procedure or a deadline — those values only ever exist because a catalog
# record supplied them.
#
# A no-coverage answer is still a SUCCESS: an honest "not in the catalog" is a
# valid outcome, not an error, and it is screened by exactly the same output
# rules as any other answer.

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import CorporateTrainingFAQAgent

_OUT_OF_DOMAIN = "how do i bake a sourdough loaf at home over the weekend"
_IN_DOMAIN = "which leadership programs can a team lead with three years of service apply for?"


def _invoke(question, input_context=None):
    ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL, caller_id="pb-out-of-scope")
    return CorporateTrainingFAQAgent().invoke(question, ctx=ctx, input_context=input_context or {})


class TestNoCoverageIsStated:
    def test_out_of_domain_question_returns_the_no_coverage_answer(self):
        result = _invoke(_OUT_OF_DOMAIN)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "does not contain a program" in result["output"]

    def test_no_coverage_answer_carries_no_programs(self):
        assert _invoke(_OUT_OF_DOMAIN)["programs"] == []

    def test_no_coverage_answer_invents_no_catalog_facts(self):
        output = _invoke(_OUT_OF_DOMAIN)["output"]
        assert "Matching programs" not in output
        assert "How to apply" not in output
        assert "Deadline" not in output
        # No verdict is claimed about a program that was never found.
        for verdict in ("Eligible", "Not eligible", "Conditionally eligible"):
            assert verdict not in output

    def test_no_coverage_answer_points_the_caller_somewhere_real(self):
        assert "training team" in _invoke(_OUT_OF_DOMAIN)["output"]


class TestCoverageStillWorks:
    """The degrade path must not be the only path — otherwise 'no invention'
    would be satisfied by an agent that never answers anything."""

    def test_in_domain_question_returns_grounded_content(self):
        result = _invoke(_IN_DOMAIN)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "Matching programs" in result["output"]
        assert result["programs"]

    def test_a_category_with_no_match_degrades_rather_than_substituting(self):
        """A filter that excludes every relevant record must yield the
        no-coverage answer, never a record from another category."""
        result = _invoke(_IN_DOMAIN, input_context={"category": "compliance"})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "Leadership Foundations" not in result["output"]
