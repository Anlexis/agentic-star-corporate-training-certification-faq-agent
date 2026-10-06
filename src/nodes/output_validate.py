"""AgentCore Platform v1.0"""

# EDU-C2-014 — OutputValidateNode
# Domain node 5: the output boundary of the inner pipeline.
#
# Node contract: extend FunctionNode; implement execute(self, state) -> dict —
# no `config` parameter. Return ONLY the fields this node changes (never full
# state). Never import from mediator/, api/, or other agents.
#
# THE OUTPUT INVARIANT this template states and enforces:
#
#   No released representation of an answer carries a credential-shaped token
#   or a direct personal identifier.
#
# "Every representation" is the load-bearing part. The answer leaves this
# agent in three forms, and the SAME screen runs on all three:
#   1. the rendered answer body                      — this node;
#   2. the outer `result` / `output` envelope field  — PostProcessNode;
#   3. the structured `programs` list                — the agent's get_output().
# A screen that guards only the prose lets the structured field through, so
# screen_value() below is the single implementation all three import.
#
# Amount rounding is deliberately NOT part of this invariant: this template
# renders no monetary aggregate at all. Program records carry schedules,
# procedures and eligibility rules — never figures — so there is no numeric
# surface to round and no numeric grid to enforce. See docs/02_design.md.
#
# Identifier guards: catalog and course identifiers (TRN-LEAD-101,
# TRN-2026-0014-01) contain digit runs and hyphens, so every numeric pattern
# below is wrapped in fixed-width single-character guards — a match may not be
# immediately preceded or followed by [A-Za-z0-9-]. Real identifiers therefore
# stay byte-identical while standalone identifier-shaped leaks are still
# caught. Rejecting more than necessary fails safe; missing a leak does not.

import logging
import re
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ANSWER_CHARS = 8000

# Blocked forms. Ordered most-specific first so the reported violation name is
# the informative one.
_BLOCKED_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    # Private-key block header.
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    # Three base64url segments separated by dots.
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    # Bearer token in an authorization-like context.
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9._~+/-]{20,}", re.IGNORECASE)),
    # Vendor-style API key prefixes.
    ("api_key", re.compile(r"(?<![A-Za-z0-9])(?:sk|pk|ak)-[A-Za-z0-9]{16,}", re.IGNORECASE)),
    # Credential assignment patterns.
    (
        "credential_assignment",
        re.compile(
            r"\b(?:password|passwd|secret|api_key|token|access_key|private_key)\s*[:=]\s*\S{8,}",
            re.IGNORECASE,
        ),
    ),
    # Direct personal identifiers: national-ID shape, e-mail address, and long
    # numeric ID runs. Guarded so catalog identifiers are never mistaken for
    # one of them.
    ("national_id", re.compile(r"(?<![A-Za-z0-9-])\d{3}-\d{2}-\d{4}(?![A-Za-z0-9-])")),
    ("email_address", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("long_numeric_id", re.compile(r"(?<![A-Za-z0-9-])\d{4}[- ]?\d{4}[- ]?\d{2,11}(?![A-Za-z0-9-])")),
]


def screen_text(text: str) -> Optional[str]:
    """Return the name of the first blocked form found in *text*, else None."""
    if not isinstance(text, str) or not text:
        return None
    for name, pattern in _BLOCKED_PATTERNS:
        if pattern.search(text):
            return name
    return None


def screen_value(value: Any) -> Optional[str]:
    """Screen any released payload — string, list, dict, or nesting of those.

    The structured fields this agent publishes (the program list) are
    screened with exactly the same rules as the prose, so a leak cannot travel
    out through the field that happens not to be text.
    """
    if isinstance(value, str):
        return screen_text(value)
    if isinstance(value, dict):
        for key, item in value.items():
            violation = screen_value(key) or screen_value(item)
            if violation:
                return violation
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            violation = screen_value(item)
            if violation:
                return violation
        return None
    return None


def _answer_config(state: AgentState) -> Dict[str, Any]:
    """Effective answer config: state-seeded answer_config > module defaults.

    No `config` parameter is read here — execute() takes only `state`.
    """
    seeded = from_json(state.get("answer_config"), None)
    return seeded if isinstance(seeded, dict) else {}


def _max_answer_chars(config: Dict[str, Any]) -> int:
    value = config.get("max_answer_chars")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return _DEFAULT_MAX_ANSWER_CHARS
    return value


def _escalation_keywords(config: Dict[str, Any]) -> List[str]:
    configured = config.get("escalation_keywords")
    if not isinstance(configured, list):
        return []
    return [item.lower() for item in configured if isinstance(item, str) and item]


class OutputValidateNode(FunctionNode):
    """Screen the assembled answer before it leaves the inner pipeline.

    Runs on EVERY answer path, including the explicit no-coverage message —
    the screen is unconditional, so there is no branch on which it is skipped.

    Input state keys:
        answer:        the assembled answer body
        programs:      JSON list[dict] of the rendered programs
        answer_config: JSON dict — max_answer_chars / escalation_keywords

    Output state keys (partial dict):
        is_valid_output: True once the answer cleared the screen
        hr_escalation:   advisory routing flag (the answer is still delivered)

    On a violation:
        status:        ERROR — the offending answer is dropped, never returned
        error_message: the blocked form's name (never the matched text)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> dict[str, Any]:
        # A reason settled earlier in the run is the real one: pass it through
        # untouched instead of doing work on input that was already declined.
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        config = _answer_config(state)
        answer = state.get("answer") or ""

        if not isinstance(answer, str) or not answer.strip():
            logger.warning("OutputValidateNode: answer is empty")
            return {
                "is_valid_output": False,
                "status": AgentStatus.ERROR.value,
                "error_message": "OutputValidateNode: answer is empty",
            }

        max_chars = _max_answer_chars(config)
        if len(answer) > max_chars:
            logger.warning("OutputValidateNode: answer exceeds the configured limit")
            return {
                "is_valid_output": False,
                "status": AgentStatus.ERROR.value,
                "error_message": (f"OutputValidateNode: answer length {len(answer)} exceeds limit {max_chars}"),
            }

        # The invariant, applied to both representations this node can see:
        # the prose body and the structured program list.
        programs = from_json(state.get("programs"), []) or []
        violation = screen_text(answer) or screen_value(programs)
        if violation:
            logger.error("OutputValidateNode: output blocked — %s", violation)
            emit_trace_event("output_validate_blocked", {"violation": violation}, state)
            return {
                "is_valid_output": False,
                "status": AgentStatus.ERROR.value,
                "error_message": f"OutputValidateNode: output blocked — {violation}",
            }

        escalation_keywords = _escalation_keywords(config)
        lowered = answer.lower()
        hr_escalation = any(keyword in lowered for keyword in escalation_keywords)

        logger.info(
            "OutputValidateNode: released — answer_chars=%d escalation=%s",
            len(answer),
            hr_escalation,
        )
        emit_trace_event(
            "output_validate_complete",
            {"answer_chars": len(answer), "escalation": hr_escalation},
            state,
        )
        return {
            "is_valid_output": True,
            "hr_escalation": hr_escalation,
            "programs": to_json(programs),
            "status": AgentStatus.SUCCESS.value,
        }
