# EDU-C2-014 — Unit tests: OutputValidateNode and the output screen
#
# The stated output invariant is that NO released representation of an answer
# carries a credential-shaped token or a direct personal identifier. These
# tests probe the screen in BOTH directions:
#   - every blocked form is caught, in prose and in the structured program
#     list (the representation a prose-only screen would miss);
#   - the identifiers this domain legitimately renders survive byte-identical,
#     because a screen that mangles catalog identifiers is a defect of the
#     same seriousness as one that leaks.

import pytest

from framework.schemas.agent_status import AgentStatus
from src.nodes.output_validate import OutputValidateNode, screen_text, screen_value
from src.schemas.state import to_json

_CLEAN = "# Corporate Training & Certification — Catalog Answer\n\nEligibility: Eligible\n"


def _run(answer=_CLEAN, programs=None, config=None):
    state = {"answer": answer, "programs": to_json(programs if programs is not None else [])}
    if config is not None:
        state["answer_config"] = to_json(config)
    return OutputValidateNode().execute(state)


class TestBlockedForms:
    @pytest.mark.parametrize(
        "leak",
        [
            "Use this key: sk-ABCDEFGHIJKLMNOP0123456789 to open the catalog.",
            "token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
            "password: hunter2hunter2",
            "Employee national ID on file: 123-45-6789.",
            "Reply to alex.morgan@example.com for details.",
            "Staff record 1234-5678-9012 is enrolled.",
            "-----BEGIN RSA PRIVATE KEY-----",
        ],
    )
    def test_leak_is_blocked_and_the_answer_is_dropped(self, leak):
        result = _run(answer=f"{_CLEAN}\n{leak}")
        assert result["status"] == AgentStatus.ERROR.value
        assert result["is_valid_output"] is False
        assert "answer" not in result, "a blocked answer must never be surfaced"
        assert result["error_message"]

    def test_error_names_the_form_not_the_matched_text(self):
        secret = "sk-ABCDEFGHIJKLMNOP0123456789"
        result = _run(answer=f"{_CLEAN}\nkey {secret}")
        assert secret not in result["error_message"]
        assert "api_key" in result["error_message"]


class TestEveryRepresentationIsScreened:
    """A screen that guards only the prose lets the structured field through."""

    def test_leak_inside_the_structured_program_list_is_blocked(self):
        programs = [
            {
                "id": "TRN-LEAD-101",
                "program_name": "Leadership Foundations",
                "category": "leadership",
                "source": "contact alex.morgan@example.com",
                "eligibility": "yes",
            }
        ]
        result = _run(programs=programs)
        assert result["status"] == AgentStatus.ERROR.value
        assert result["is_valid_output"] is False

    def test_screen_value_walks_nested_structures(self):
        assert screen_value({"a": [{"b": ("123-45-6789",)}]}) == "national_id"
        assert screen_value({"a": [{"b": ("TRN-LEAD-101",)}]}) is None

    def test_screen_value_checks_keys_as_well_as_values(self):
        assert screen_value({"alex.morgan@example.com": "ok"}) == "email_address"


class TestDomainIdentifiersSurvive:
    """Both directions: the catalog and course identifiers this domain renders
    every day must pass through untouched."""

    @pytest.mark.parametrize(
        "identifier",
        [
            "TRN-LEAD-101",
            "TRN-COMP-050",
            "TRN-2026-0014-01",
            "EDU-C2-014",
            "STU-1234",
            "G3",
            "2026-03-31",
            "90d",
            "cohort 2026",
            "45 hours",
            "one credit is 45 hours of student work",
        ],
    )
    def test_identifier_is_not_flagged(self, identifier):
        assert screen_text(f"Program {identifier} runs quarterly.") is None

    @pytest.mark.parametrize(
        "identifier",
        ["TRN-LEAD-101", "TRN-2026-0014-01", "2026-03-31"],
    )
    def test_identifier_reaches_the_caller_byte_identical(self, identifier):
        answer = f"{_CLEAN}\n- [1] **{identifier}** — cohort start 2026-03-31\n"
        result = _run(answer=answer)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["is_valid_output"] is True


class TestLengthAndEscalation:
    def test_empty_answer_is_rejected(self):
        result = _run(answer="   ")
        assert result["status"] == AgentStatus.ERROR.value
        assert result["is_valid_output"] is False

    def test_overlong_answer_is_rejected(self):
        result = _run(answer="x" * 200, config={"max_answer_chars": 50})
        assert result["status"] == AgentStatus.ERROR.value
        assert "exceeds limit" in result["error_message"]

    def test_escalation_keyword_raises_the_advisory_flag(self):
        result = _run(
            answer=f"{_CLEAN}\nFile a grievance with the training team.",
            config={"escalation_keywords": ["grievance", "waiver"]},
        )
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["hr_escalation"] is True
        assert result["is_valid_output"] is True, "escalation is advisory — the answer is still delivered"

    def test_clean_answer_does_not_raise_the_flag(self):
        result = _run(config={"escalation_keywords": ["grievance", "waiver"]})
        assert result["hr_escalation"] is False
        assert result["status"] == AgentStatus.SUCCESS.value
