"""AgentCore Platform v1.0"""

# State must be a flat TypedDict — never a Pydantic BaseModel. LangGraph
# checkpoints use msgpack serialization; Pydantic objects cause silent
# corruption. Extend AgentState with agent-specific fields only. Do NOT add
# credentials, secrets, or Pydantic models.
#
# Msgpack safety: structured fields (dict / list[dict]) are stored as JSON
# STRINGS, not bare Python containers — a bare dict/list in a checkpointed
# State field corrupts silently. Producers serialize with to_json() on write;
# consumers deserialize with from_json() on read.
#
# EDU-C2-014 — Corporate Training & Certification FAQ Agent (Cat 2, nested).
# Two-layer nested Cat 2 graph: outer backbone (AgentBaseGraph) + inner domain
# workflow (BaseGraph). The fields below cover both layers.
#
# Personal-data note: the employee attributes this agent accepts are
# organisational only — department, role, grade, and years of service, each
# locked to an inert identifier or a bounded number by the caller-contract
# gate. No employee name, staff number, contact address, or personnel-record
# content is read, stored, or rendered anywhere in State, and direct
# identifiers pasted into the free-text question are surface-stripped by
# PreProcessNode before any field is written.

import json
from typing import Any, Optional

from framework.schemas.agent_state import AgentState


def to_json(value: Any) -> Optional[str]:
    """Serialize a dict/list State field to a JSON string (msgpack safety).

    None passes through unchanged so an 'unset' field stays distinguishable
    from an empty container.
    """
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Optional[str], default: Any = None) -> Any:
    """Deserialize a JSON-string State field back to its dict/list.

    None / empty / malformed input returns the supplied ``default`` so a
    missing or corrupt field is non-fatal for the consuming node.
    """
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class State(AgentState):
    """Flat TypedDict for EDU-C2-014.

    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, etc.) are inherited from AgentState. Domain fields are
    Optional and unset at graph initialisation — nodes write them as the run
    progresses (runtime TypedDicts do not enforce key presence; consumers
    always read via state.get()).
    """

    # ------------------------------------------------------------------
    # Outer layer — PreProcessNode / TrainingFAQGraphNode.merge_output
    # ------------------------------------------------------------------

    # Identifier-stripped, validated question payload written by
    # PreProcessNode. The raw input is not persisted beyond that node.
    validated_input: Optional[str]

    # Final rendered FAQ answer, mapped from the inner graph's answer output
    # via merge_output().
    training_faq_answer: Optional[str]

    # ------------------------------------------------------------------
    # Inner layer — domain nodes (DomainWorkflowGraph)
    # ------------------------------------------------------------------

    # InputValidateNode outputs.
    # The employee question after the caller-contract gate: whitespace
    # collapsed and length-capped. Drives retrieval; never echoed into the
    # rendered answer.
    question: Optional[str]

    # JSON STRING (to_json) of the validated caller parameters. Deserialised
    # shape: {"category": str | None, "top_k": int | None,
    # "min_score": float | None, "language": str | None,
    # "employee_profile": {"department": str | None, "role": str | None,
    # "grade": str | None, "years_of_service": float | None} | None}.
    # Every value has already passed the bounded / inert-identifier checks.
    query_filters: Optional[str]

    # Runtime `retrieval` block (config/config.yaml) forwarded by
    # TrainingFAQGraphNode._parent_config() into
    # DomainWorkflowGraph._extra_initial_state(). JSON STRING (to_json) of
    # {"top_k": int, "score_threshold": float, "kb_path": str}. This is the
    # ONLY route config reaches KBRetrieveNode — execute() takes no `config`
    # parameter.
    retrieval_config: Optional[str]

    # Runtime `answer` block (config/config.yaml), same route. JSON STRING
    # (to_json) of {"max_answer_chars": int, "supported_languages": list[str],
    # "escalation_keywords": list[str]}. Read by QueryNormalizeNode,
    # ResponseGenerateNode and OutputValidateNode.
    answer_config: Optional[str]

    # QueryNormalizeNode outputs.
    # Normalized query text (NFC, whitespace-collapsed, lowercased for EN).
    normalized_query: Optional[str]

    # Detected or caller-selected language: "ja", "vi" or "en".
    detected_language: Optional[str]

    # KBRetrieveNode outputs.
    # JSON STRING (to_json) of the scored catalog matches. Deserialised shape:
    # list[dict], each entry {"id", "program_name", "category", "source",
    # "score", "content", "application_procedure", "deadline",
    # "eligibility"}.
    retrieved_docs: Optional[str]

    # True when no catalog program cleared the relevance floor.
    out_of_scope: Optional[bool]

    # ResponseGenerateNode outputs.
    # Rendered answer body assembled from the retrieved catalog records.
    answer: Optional[str]

    # Eligibility verdict for the matched programs:
    # "yes" | "no" | "conditional" | "unknown".
    eligibility_result: Optional[str]

    # Application procedure quoted from the best-matching catalog record.
    application_procedure: Optional[str]

    # Deadline or schedule text quoted from the best-matching catalog record.
    deadline_info: Optional[str]

    # JSON STRING (to_json) of the program summaries rendered in the answer.
    # Deserialised shape: list[dict], each entry {"id", "program_name",
    # "category", "source", "eligibility"}. Re-surfaced at the outer layer so
    # get_output() can publish it without reaching into the inner graph.
    programs: Optional[str]

    # OutputValidateNode outputs.
    # True once the answer has cleared the output screen.
    is_valid_output: Optional[bool]

    # True when the answer touches a case the training team must handle
    # directly (advisory only — the answer is still delivered).
    hr_escalation: Optional[bool]

    # Non-fatal intake notes accumulated while parsing the request (no
    # personal data, no caller values). JSON STRING (to_json) of list[str].
    intake_notes: Optional[str]

    # ------------------------------------------------------------------
    # Tracing / audit — framework-managed; do NOT write from node code
    # ------------------------------------------------------------------

    trace_id: Optional[str]
    correlation_id: Optional[str]
    error_code: Optional[str]
