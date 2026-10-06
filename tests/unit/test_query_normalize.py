# EDU-C2-014 — Unit tests: QueryNormalizeNode
#
# Language resolution order (caller selection beats the heuristic), unicode
# normalization, and the configured supported-language list arriving through
# the state-seeded answer_config field rather than a dead config parameter.

from src.nodes.query_normalize import QueryNormalizeNode
from src.schemas.state import to_json


def _run(question, filters=None, answer_config=None):
    state = {"question": question}
    if filters is not None:
        state["query_filters"] = to_json(filters)
    if answer_config is not None:
        state["answer_config"] = to_json(answer_config)
    return QueryNormalizeNode().execute(state)


class TestLanguageResolution:
    def test_japanese_text_detected(self):
        result = _run("リーダーシップ研修の申請方法を教えてください")
        assert result["detected_language"] == "ja"
        assert result["normalized_query"]

    def test_vietnamese_text_detected(self):
        result = _run("chương trình đào tạo lãnh đạo có điều kiện gì")
        assert result["detected_language"] == "vi"

    def test_english_text_detected_and_lowercased(self):
        result = _run("How Do I Apply For The Leadership Program?")
        assert result["detected_language"] == "en"
        assert result["normalized_query"] == "how do i apply for the leadership program?"

    def test_caller_selection_beats_the_heuristic(self):
        """A validated `language` filter is the caller's explicit choice."""
        result = _run("リーダーシップ研修", filters={"language": "en"})
        assert result["detected_language"] == "en"

    def test_unsupported_language_falls_back_to_english(self):
        result = _run("研修の申請方法を教えてください", answer_config={"supported_languages": ["en"]})
        assert result["detected_language"] == "en"

    def test_caller_selection_outside_the_supported_list_is_ignored(self):
        result = _run("how do i apply", filters={"language": "ja"}, answer_config={"supported_languages": ["en"]})
        assert result["detected_language"] == "en"


class TestNormalization:
    def test_whitespace_is_collapsed(self):
        assert _run("  training    catalog   query  ")["normalized_query"] == "training catalog query"

    def test_empty_question_degrades_to_an_empty_query(self):
        result = _run("   ")
        assert result["normalized_query"] == ""
        assert result["detected_language"] == "en"

    def test_decomposed_input_is_nfc_normalised(self):
        """Decomposed input must normalise to the composed form so retrieval
        tokenisation sees one spelling, not two."""
        decomposed = "de\u0301veloppement leadership"
        composed = "d\u00e9veloppement leadership"
        assert decomposed != composed
        assert _run(decomposed)["normalized_query"] == composed
