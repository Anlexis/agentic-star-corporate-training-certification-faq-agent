# EDU-C2-014 — Unit tests: ResponseGenerateNode
#
# The eligibility verdict is the one value this node computes, so it is
# exercised across all four outcomes, including the two that are easy to get
# wrong: "unknown" when no profile was supplied (nothing may be claimed) and
# "conditional" when the record states a rule the profile does not answer.
#
# The other assertion class is the output invariant on the rendering side: the
# answer quotes the catalog and never the caller.

from src.nodes.response_generate import ResponseGenerateNode
from src.schemas.state import from_json, to_json

_RECORD = {
    "id": "TRN-LEAD-101",
    "program_name": "Leadership Foundations",
    "category": "leadership",
    "source": "Corporate training catalog, leadership development section",
    "score": 0.9,
    "content": "A twelve-week blended program for newly appointed team leads.",
    "application_procedure": "Submit the leadership nomination form through the learning portal.",
    "deadline": "Applications close six weeks before each quarterly cohort start.",
    "eligibility": {
        "min_years_of_service": 2,
        "departments": [],
        "roles": ["manager", "lead"],
        "grades": [],
    },
}


def _run(records=None, profile=None, language="en", out_of_scope=False):
    state = {
        "detected_language": language,
        "out_of_scope": out_of_scope,
        "retrieved_docs": to_json(records if records is not None else [_RECORD]),
        "query_filters": to_json({"employee_profile": profile}),
    }
    return ResponseGenerateNode().execute(state)


class TestEligibilityVerdict:
    def test_no_profile_claims_nothing(self):
        assert _run(profile=None)["eligibility_result"] == "unknown"

    def test_satisfied_rules_are_eligible(self):
        result = _run(profile={"role": "manager", "years_of_service": 4.0, "department": None, "grade": None})
        assert result["eligibility_result"] == "yes"

    def test_violated_role_rule_is_not_eligible(self):
        result = _run(profile={"role": "intern", "years_of_service": 9.0, "department": None, "grade": None})
        assert result["eligibility_result"] == "no"

    def test_short_tenure_is_not_eligible(self):
        result = _run(profile={"role": "manager", "years_of_service": 1.0, "department": None, "grade": None})
        assert result["eligibility_result"] == "no"

    def test_unanswered_rule_is_conditional(self):
        """The record states a tenure rule; the profile does not answer it, so
        nothing stronger than 'conditional' may be claimed."""
        result = _run(profile={"role": "manager", "years_of_service": None, "department": None, "grade": None})
        assert result["eligibility_result"] == "conditional"

    def test_open_dimensions_are_not_invented(self):
        """An empty department list means the program is open on that
        dimension — an unspecified department must not make it conditional."""
        result = _run(profile={"role": "lead", "years_of_service": 3.0, "department": None, "grade": None})
        assert result["eligibility_result"] == "yes"


class TestAnswerAssembly:
    def test_answer_quotes_the_catalog_record(self):
        answer = _run()["answer"]
        assert "Leadership Foundations" in answer
        assert "twelve-week blended program" in answer
        assert "TRN-LEAD-101" in answer
        assert "Corporate training catalog" in answer

    def test_answer_never_echoes_the_caller_question(self):
        """Caller free text has no rendered surface at all — the question is a
        retrieval key, not answer content."""
        marker = "zzz-caller-marker-zzz"
        state = {
            "detected_language": "en",
            "question": marker,
            "user_input": marker,
            "normalized_query": marker,
            "retrieved_docs": to_json([_RECORD]),
            "query_filters": to_json({}),
        }
        assert marker not in ResponseGenerateNode().execute(state)["answer"]

    def test_procedure_and_deadline_are_quoted_from_the_best_match(self):
        result = _run()
        assert result["application_procedure"] == _RECORD["application_procedure"]
        assert result["deadline_info"] == _RECORD["deadline"]

    def test_programs_field_mirrors_the_rendered_records(self):
        programs = from_json(_run()["programs"], [])
        assert [p["id"] for p in programs] == ["TRN-LEAD-101"]
        assert programs[0]["eligibility"] == "unknown"

    def test_result_mirrors_the_answer_for_the_outer_slot(self):
        result = _run()
        assert result["result"] == result["answer"]


class TestNoCoveragePath:
    def test_out_of_scope_states_the_gap_without_inventing_a_program(self):
        result = _run(out_of_scope=True, records=[])
        assert result["eligibility_result"] == "unknown"
        assert from_json(result["programs"], []) == []
        answer = result["answer"]
        assert "does not contain a program" in answer
        assert "Leadership Foundations" not in answer

    def test_empty_record_set_takes_the_same_path(self):
        assert _run(records=[])["eligibility_result"] == "unknown"


class TestLocalisation:
    def test_japanese_answer_uses_japanese_furniture(self):
        answer = _run(language="ja")["answer"]
        assert "社内研修" in answer
        assert "Leadership Foundations" in answer  # catalog content is not translated

    def test_vietnamese_answer_uses_vietnamese_furniture(self):
        assert "Đào tạo" in _run(language="vi")["answer"]

    def test_unknown_language_falls_back_to_english(self):
        assert "Corporate Training" in _run(language="klingon")["answer"]
