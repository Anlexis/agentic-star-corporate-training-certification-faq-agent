"""AgentCore Platform v1.0"""

# EDU-C2-014 — InputValidateNode
# Domain node 1: the caller-data contract gate. It parses the incoming
# training-catalog question and validates every caller-controlled parameter
# against explicit bounds — fail CLOSED.
#
# Node contract: extend FunctionNode; implement execute(self, state) -> dict —
# no `config` parameter. Return ONLY the fields this node changes (never full
# state). Never import from mediator/, api/, or other agents.
#
# Caller parameters arrive on the structured invocation channel
# (`input_context`, bridged from the outer graph — see
# src/graph/context_bridge.py):
#
#     {"category": "<slug>", "top_k": <int>, "min_score": <float>,
#      "language": "<slug>",
#      "employee_profile": {"department": "<slug>", "role": "<slug>",
#                           "grade": "<slug>", "years_of_service": <number>}}
#
# Validation contract (fail CLOSED, field-naming errors only):
#   - top_k: integer within 1..20. Booleans are rejected explicitly
#     (isinstance(True, int) is True in Python). Numeric strings, floats and
#     out-of-range values reject the request.
#   - min_score and years_of_service: real numbers that must be FINITE and in
#     range. NaN and Infinity parse fine via float() and Python's json even
#     accepts bare NaN / Infinity in a request body, and every NaN comparison
#     evaluates False — so an unchecked non-finite value silently disables the
#     relevance floor or the tenure rule, which is exactly the decision this
#     template exists to make. They are rejected.
#   - category, language, department, role, grade: caller strings that select
#     behaviour and would otherwise reach logs and the answer. Each is locked
#     to an inert identifier ([a-z0-9_]{1,32}); category and language must in
#     addition be one of the fixed sets.
#   - absent fields are fine: the search runs with the configured defaults.
#   - question: refused when it carries an instruction-override payload
#     (text addressed to the model rather than to the training catalog). The
#     platform input gate refuses these too, but the template MUST NOT depend
#     on that: where that gate is absent or configured off, an unchecked
#     payload would reach the answer path and return success. Refusal is
#     stated in terms of BEHAVIOUR (error status, no answer, no programs),
#     never a gate's wording.
#
# A rejected VALUE is never echoed — errors name the field only.
# On rejection the framework's ERROR short-circuit skips every downstream
# node, so no retrieval or answer assembly runs on unvalidated data.

import math
import re
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.progress import emit_progress
from src.services.failure_message import INPUT_REJECTED
from src.schemas.state import to_json

# Hard cap on the normalised question length (defence-in-depth on input size).
_MAX_QUESTION_CHARS = 2000
_MIN_QUESTION_CHARS = 3

# Bounds for the caller-supplied retrieval overrides.
_TOP_K_MIN = 1
_TOP_K_MAX = 20
_MIN_SCORE_MIN = 0.0
_MIN_SCORE_MAX = 1.0

# Bounds for the caller-supplied tenure attribute (years).
_YEARS_MIN = 0.0
_YEARS_MAX = 60.0

# Caller strings that select behaviour must be inert identifiers — lowercase
# alphanumerics/underscore, bounded length. Free text in such a field is
# caller-controlled output and log injection, and is rejected before use.
_INERT_IDENTIFIER_RE = re.compile(r"^[a-z0-9_]{1,32}$")

# The fixed catalog categories this template's corpus is organised around
# (docs/02_design.md). Every slug satisfies _INERT_IDENTIFIER_RE.
VALID_CATEGORIES: List[str] = [
    "leadership",
    "compliance",
    "technical_skills",
    "certification",
    "reskilling",
]

# The answer languages this template renders.
VALID_LANGUAGES: List[str] = ["ja", "vi", "en"]

# Organisational attributes accepted on the employee profile. Names, staff
# numbers and contact details are deliberately NOT part of the contract.
_PROFILE_IDENTIFIER_FIELDS: List[str] = ["department", "role", "grade"]

# Instruction-override phrasings: text addressed to the MODEL rather than a
# question addressed to the training catalog. Deliberately narrow — a genuine
# question containing these words ("which course explains our system prompt
# policy?") does not match, because each alternative requires the imperative
# override shape.
_INJECTION_RE = re.compile(
    r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+"
    r"(?:instruction|instructions|prompt|prompts|rule|rules|direction|directions)"
    r"|disregard\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+"
    r"(?:instruction|instructions|prompt|prompts|rule|rules)"
    r"|forget\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+"
    r"(?:instruction|instructions|prompt|prompts)"
    r"|(?:reveal|show|print|repeat|output|disclose)\s+(?:me\s+)?(?:your|the)\s+"
    r"(?:system\s+prompt|system\s+message|instructions|initial\s+prompt)"
    r"|you\s+are\s+now\s+(?:a|an)\s"
    r"|act\s+as\s+(?:if\s+you\s+are\s+)?(?:a\s+|an\s+)?(?:developer|admin|root)\s+mode"
    r"|override\s+(?:your|the)\s+(?:instruction|instructions|rules|safety)",
    re.IGNORECASE,
)

_WHITESPACE_RE = re.compile(r"\s+")


def _finite_in_range(value: Any, field: str, low: float, high: float) -> Tuple[Optional[float], Optional[str]]:
    """Validate an untrusted caller number. Returns (value, error).

    Fail-closed: only a real, FINITE number inside [low, high] is accepted.
    Booleans and non-numeric types are rejected outright, and NaN / +-Infinity
    are rejected explicitly — they survive float() and every comparison
    against them is False, so an unchecked non-finite value would pass a naive
    range test and disable the rule it governs. The offending VALUE is never
    echoed.
    """
    if value is None:
        return None, None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, f"InputValidateNode: {field} must be a number between {low:g} and {high:g}."
    number = float(value)
    if not math.isfinite(number):
        return None, f"InputValidateNode: {field} must be a finite number between {low:g} and {high:g}."
    if not low <= number <= high:
        return None, f"InputValidateNode: {field} must be a number between {low:g} and {high:g}."
    return number, None


def _validate_top_k(value: Any) -> Tuple[Optional[int], Optional[str]]:
    """Validate the untrusted caller top_k. Returns (value, error).

    Fail-closed: only a plain integer within [1, 20] is accepted. Booleans
    (isinstance(True, int) is True), floats, numeric strings and out-of-range
    integers all return a field-naming error.
    """
    if value is None:
        return None, None
    error = f"InputValidateNode: top_k must be an integer between {_TOP_K_MIN} and {_TOP_K_MAX}."
    if isinstance(value, bool) or not isinstance(value, int):
        return None, error
    if not _TOP_K_MIN <= value <= _TOP_K_MAX:
        return None, error
    return value, None


def _validate_choice(value: Any, field: str, allowed: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """Validate a caller string against a fixed set of inert identifiers.

    Fail-closed: the candidate must already be an inert identifier before it
    is compared against the fixed set, and a rejected value is never echoed
    into the error.
    """
    if value is None:
        return None, None
    error = f"InputValidateNode: {field} must be one of the fixed {field} values."
    if not isinstance(value, str):
        return None, error
    candidate = value.strip().lower()
    if not candidate:
        return None, None
    if not _INERT_IDENTIFIER_RE.match(candidate) or candidate not in allowed:
        return None, error
    return candidate, None


def _validate_identifier(value: Any, field: str) -> Tuple[Optional[str], Optional[str]]:
    """Validate a free-form organisational attribute as an inert identifier.

    Departments, roles and grades vary per deployment, so they are not held
    against a fixed list — but they are still caller strings that reach the
    retrieval filter and the audit log, so they must be inert.
    """
    if value is None:
        return None, None
    error = f"InputValidateNode: {field} must be a short lowercase identifier."
    if not isinstance(value, str):
        return None, error
    candidate = value.strip().lower()
    if not candidate:
        return None, None
    if not _INERT_IDENTIFIER_RE.match(candidate):
        return None, error
    return candidate, None


class InputValidateNode(FunctionNode):
    """Parse the request into a normalised question plus validated filters.

    Input state keys:
        validated_input | user_input: identifier-stripped question payload
        input_context:                structured invocation parameters,
                                      bridged from the outer graph

    Output state keys (partial dict):
        question:      normalised free-text question
        query_filters: JSON dict of the validated caller parameters
        intake_notes:  (when non-fatal anomalies were seen) JSON list[str]

    On a caller-contract violation:
        status:    ERROR (fail-closed; downstream nodes are skipped)
        error_log: one field-naming message per violation — values are never
                   echoed
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> dict[str, Any]:
        raw = state.get("validated_input") or state.get("user_input", "")
        input_context = state.get("input_context", {}) or {}
        notes: List[str] = []
        errors: List[str] = []

        # ------------------------------------------------------------------
        # The free-text question
        # ------------------------------------------------------------------
        question = raw.strip() if isinstance(raw, str) else ""
        if not question:
            notes.append("InputValidateNode: empty request — no question to answer.")
        elif len(question) < _MIN_QUESTION_CHARS:
            errors.append(f"InputValidateNode: question must be at least {_MIN_QUESTION_CHARS} characters.")

        # Instruction-override payloads are refused here, before any retrieval
        # or answer assembly, independently of the platform gate.
        if question and _INJECTION_RE.search(question):
            errors.append("InputValidateNode: question refused — instruction-override content.")

        # ------------------------------------------------------------------
        # Structured invocation parameters (input_context)
        # ------------------------------------------------------------------
        if not isinstance(input_context, dict):
            # A non-mapping input_context cannot carry a valid contract.
            errors.append("InputValidateNode: input_context must be an object.")
            input_context = {}

        category, category_err = _validate_choice(input_context.get("category"), "category", VALID_CATEGORIES)
        if category_err:
            errors.append(category_err)

        language, language_err = _validate_choice(input_context.get("language"), "language", VALID_LANGUAGES)
        if language_err:
            errors.append(language_err)

        top_k, top_k_err = _validate_top_k(input_context.get("top_k"))
        if top_k_err:
            errors.append(top_k_err)

        min_score, min_score_err = _finite_in_range(
            input_context.get("min_score"), "min_score", _MIN_SCORE_MIN, _MIN_SCORE_MAX
        )
        if min_score_err:
            errors.append(min_score_err)

        # ------------------------------------------------------------------
        # Employee profile — organisational attributes only
        # ------------------------------------------------------------------
        profile: Optional[Dict[str, Any]] = None
        raw_profile = input_context.get("employee_profile")
        if raw_profile is not None:
            if not isinstance(raw_profile, dict):
                errors.append("InputValidateNode: employee_profile must be an object.")
            else:
                profile = {}
                for field in _PROFILE_IDENTIFIER_FIELDS:
                    attribute, attribute_err = _validate_identifier(raw_profile.get(field), field)
                    if attribute_err:
                        errors.append(attribute_err)
                    profile[field] = attribute
                years, years_err = _finite_in_range(
                    raw_profile.get("years_of_service"), "years_of_service", _YEARS_MIN, _YEARS_MAX
                )
                if years_err:
                    errors.append(years_err)
                profile["years_of_service"] = years
                if not any(value is not None for value in profile.values()):
                    profile = None

        if errors:
            # Fail CLOSED: nothing downstream runs on an invalid caller contract.
            # Deduplicate while preserving order.
            emit_progress(INPUT_REJECTED)
            # Values the caller can correct: the retrieval path stays closed and
            # the run COMPLETES carrying the reason so the request can be sent
            # again with usable values.
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": "INVALID_REQUEST",
                "error_log": list(dict.fromkeys(errors)),
            }

        # Normalise whitespace and cap length.
        question = _WHITESPACE_RE.sub(" ", question).strip()
        if len(question) > _MAX_QUESTION_CHARS:
            question = question[:_MAX_QUESTION_CHARS]
            notes.append(f"InputValidateNode: question truncated to {_MAX_QUESTION_CHARS} chars.")

        filters: Dict[str, Any] = {
            "category": category,
            "top_k": top_k,
            "min_score": min_score,
            "language": language,
            "employee_profile": profile,
        }

        # Domain audit: request parsed, validated and normalised. Counts and
        # flags only — no caller values.
        emit_trace_event(
            "input_validate_complete",
            {
                "question_chars": len(question),
                "has_category_filter": category is not None,
                "has_top_k_override": top_k is not None,
                "has_min_score_override": min_score is not None,
                "has_employee_profile": profile is not None,
            },
            state,
        )

        out: Dict[str, Any] = {
            "question": question,
            "query_filters": to_json(filters),
        }
        if notes:
            out["intake_notes"] = to_json(notes)
        return out
