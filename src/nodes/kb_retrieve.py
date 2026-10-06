"""AgentCore Platform v1.0"""

# EDU-C2-014 — KBRetrieveNode
# Domain node 3: retrieve the training-catalog programs that match the
# normalized query, honouring the validated caller filters.
#
# Node contract: extend FunctionNode; implement execute(self, state) -> dict —
# no `config` parameter. Return ONLY the fields this node changes (never full
# state). Never import from mediator/, api/, or other agents.
#
# Config: `top_k`, `score_threshold` and `kb_path` are read from the
# state-seeded `retrieval_config` field (republished by
# DomainWorkflowGraph._extra_initial_state(), forwarded from
# TrainingFAQGraphNode._parent_config()), with module defaults that mirror the
# `retrieval` block in config/config.yaml. Caller overrides (`top_k`,
# `min_score`, `category`) come from the already-validated `query_filters`
# field and are bounded there.
#
# Zero-match path: out_of_scope=True with a SUCCESS status — an unanswerable
# question is a valid outcome, not an error, and the output gate renders the
# explicit no-coverage message for it.

import logging
import math
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json
from src.services.catalog_service import excerpt, load_catalog, score_record, tokenize

logger = logging.getLogger(__name__)

# Defaults mirror the `retrieval` block in config/config.yaml.
_DEFAULT_RETRIEVAL: Dict[str, Any] = {
    "top_k": 4,
    "score_threshold": 0.25,
    "kb_path": "config/kb/training_catalog_kb.json",
}


def _resolve_retrieval_config(state: AgentState) -> Dict[str, Any]:
    """Effective retrieval config: state-seeded retrieval_config > defaults.

    No `config` parameter is read here — execute() takes only `state`. Config
    knobs reach this node exclusively via the state-seeded `retrieval_config`
    field, so a value declared in config/config.yaml genuinely governs the run.
    """
    effective = dict(_DEFAULT_RETRIEVAL)  # local copy — never mutate the module default
    seeded = from_json(state.get("retrieval_config"), None)
    if isinstance(seeded, dict):
        effective.update(seeded)
    return effective


def _coerce_positive_int(value: Any, fallback: int) -> int:
    """Coerce a configured value to a positive int, falling back on nonsense."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return fallback
    return int(value)


def _coerce_unit_float(value: Any, fallback: float) -> float:
    """Coerce a configured value to a finite score in [0, 1].

    A configured value is trusted no further than a caller's: a non-finite
    threshold would compare False against every score and silently disable the
    relevance floor, so it falls back instead.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        return fallback
    return number


def _eligibility_block(record: Dict[str, Any]) -> Dict[str, Any]:
    """Return the record's eligibility block as a plain dict."""
    block = record.get("eligibility")
    return block if isinstance(block, dict) else {}


class KBRetrieveNode(FunctionNode):
    """Retrieve matching training-catalog programs for the normalized query.

    Input state keys:
        normalized_query: normalized query text (QueryNormalizeNode)
        query_filters:    JSON dict — validated caller category / top_k /
                          min_score
        retrieval_config: JSON dict — top_k / score_threshold / kb_path

    Output state keys (partial dict):
        retrieved_docs: JSON list[dict] of scored catalog records
        out_of_scope:   True when nothing cleared the relevance floor
        intake_notes:   (when the catalog could not be read) JSON list[str]
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> dict[str, Any]:
        # A reason settled earlier in the run is the real one: pass it through
        # untouched instead of doing work on input that was already declined.
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        retrieval = _resolve_retrieval_config(state)
        filters: Dict[str, Any] = from_json(state.get("query_filters"), {}) or {}

        # Caller overrides win over the configured defaults; both were bounded
        # before they reached this node (InputValidateNode / config coercion).
        top_k = _coerce_positive_int(filters.get("top_k"), _coerce_positive_int(retrieval.get("top_k"), 4))
        threshold = _coerce_unit_float(
            filters.get("min_score"), _coerce_unit_float(retrieval.get("score_threshold"), 0.25)
        )
        category: Optional[str] = filters.get("category") if isinstance(filters.get("category"), str) else None

        normalized_query = state.get("normalized_query") or ""
        if not isinstance(normalized_query, str) or not normalized_query.strip():
            emit_trace_event("kb_retrieve_complete", {"match_count": 0, "no_query": True}, state)
            return {
                "retrieved_docs": to_json([]),
                "out_of_scope": True,
                "status": AgentStatus.SUCCESS.value,
            }

        kb_path = retrieval.get("kb_path")
        records, notes = load_catalog(kb_path if isinstance(kb_path, str) else str(_DEFAULT_RETRIEVAL["kb_path"]))

        query_tokens = tokenize(normalized_query)
        scored: List[Dict[str, Any]] = []
        for record in records:
            if category is not None and str(record.get("category", "")) != category:
                continue
            score = score_record(record, query_tokens)
            if score < threshold:
                continue
            scored.append(
                {
                    "id": str(record.get("id", "")),
                    "program_name": str(record.get("program_name", "")),
                    "category": str(record.get("category", "")),
                    "source": str(record.get("source", "")),
                    "score": round(score, 4),
                    "content": excerpt(record),
                    "application_procedure": str(record.get("application_procedure", "")),
                    "deadline": str(record.get("deadline", "")),
                    "eligibility": _eligibility_block(record),
                }
            )

        # Deterministic ordering: score first, then catalog id so equal scores
        # never reorder between runs.
        scored.sort(key=lambda item: (-float(item["score"]), str(item["id"])))
        matches = scored[:top_k]

        logger.info(
            "KBRetrieveNode: matches=%d top_k=%d threshold=%.2f category_filter=%s",
            len(matches),
            top_k,
            threshold,
            category is not None,
        )
        emit_trace_event(
            "kb_retrieve_complete",
            {"match_count": len(matches), "top_k": top_k, "has_category_filter": category is not None},
            state,
        )

        out: Dict[str, Any] = {
            "retrieved_docs": to_json(matches),
            "out_of_scope": not matches,
            "status": AgentStatus.SUCCESS.value,
        }
        if notes:
            existing = from_json(state.get("intake_notes"), []) or []
            out["intake_notes"] = to_json(list(existing) + notes)
        return out
