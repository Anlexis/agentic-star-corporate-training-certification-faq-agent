# EDU-C2-014 — Unit tests: KBRetrieveNode
#
# Retrieval runs against the catalog shipped with the template, so these tests
# assert real matching behaviour rather than a mocked retriever: relevance
# ordering, the category filter, the caller's top_k and min_score overrides,
# the zero-match degrade, and the fact that a configured value genuinely
# reaches the node through the state-seeded config field.

from src.nodes.kb_retrieve import KBRetrieveNode
from src.schemas.state import from_json, to_json


def _run(query, filters=None, retrieval=None):
    state = {"normalized_query": query}
    if filters is not None:
        state["query_filters"] = to_json(filters)
    if retrieval is not None:
        state["retrieval_config"] = to_json(retrieval)
    return KBRetrieveNode().execute(state)


def _docs(result):
    return from_json(result["retrieved_docs"], [])


class TestRetrieval:
    def test_domain_question_matches_catalog_programs(self):
        result = _run("leadership program for a new manager")
        docs = _docs(result)
        assert result["out_of_scope"] is False
        assert docs
        assert all(doc["score"] >= 0.25 for doc in docs)
        assert any(doc["id"] == "TRN-LEAD-101" for doc in docs)

    def test_results_are_ordered_by_score(self):
        docs = _docs(_run("compliance refresher policy privacy"))
        scores = [doc["score"] for doc in docs]
        assert scores == sorted(scores, reverse=True)

    def test_out_of_domain_question_degrades_to_no_coverage(self):
        result = _run("how do i bake a sourdough loaf at home")
        assert result["out_of_scope"] is True
        assert _docs(result) == []

    def test_empty_query_degrades_to_no_coverage(self):
        result = _run("   ")
        assert result["out_of_scope"] is True
        assert _docs(result) == []

    def test_retrieved_record_carries_the_catalog_fields(self):
        doc = _docs(_run("certification exam support"))[0]
        assert set(doc) == {
            "id",
            "program_name",
            "category",
            "source",
            "score",
            "content",
            "application_procedure",
            "deadline",
            "eligibility",
        }


class TestCallerOverrides:
    def test_category_filter_restricts_the_result_set(self):
        unfiltered = _docs(_run("mandatory refresher and workshop training"))
        filtered = _docs(_run("mandatory refresher and workshop training", filters={"category": "compliance"}))
        assert filtered
        assert {doc["category"] for doc in filtered} == {"compliance"}
        assert len(filtered) <= len(unfiltered)

    def test_top_k_caps_the_result_set(self):
        assert len(_docs(_run("program training", filters={"top_k": 1}))) == 1

    def test_min_score_raises_the_relevance_floor(self):
        wide = _docs(_run("leadership training program"))
        strict = _docs(_run("leadership training program", filters={"min_score": 0.9}))
        assert len(strict) < len(wide)
        assert all(doc["score"] >= 0.9 for doc in strict)


class TestConfigReachesTheNode:
    """A value declared in config/config.yaml must actually govern the run —
    the node takes no `config` parameter, so the state-seeded field is the
    only live route."""

    def test_seeded_top_k_is_honoured(self):
        assert len(_docs(_run("program training leadership", retrieval={"top_k": 2}))) <= 2

    def test_seeded_threshold_is_honoured(self):
        assert _run("leadership program", retrieval={"score_threshold": 0.99})["out_of_scope"] is True

    def test_missing_catalog_degrades_instead_of_raising(self):
        result = _run("leadership", retrieval={"kb_path": "config/kb/does_not_exist.json"})
        assert result["out_of_scope"] is True
        assert any("catalog" in note for note in from_json(result["intake_notes"], []))

    def test_nonsense_config_falls_back_to_defaults(self):
        """A malformed configured value is coerced, never trusted — a NaN
        threshold would otherwise compare False against every score."""
        result = _run("leadership program", retrieval={"top_k": "many", "score_threshold": float("nan")})
        assert result["out_of_scope"] is False
        assert _docs(result)
