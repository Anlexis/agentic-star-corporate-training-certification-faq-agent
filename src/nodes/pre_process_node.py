"""AgentCore Platform v1.0"""

# EDU-C2-014 — PreProcessNode (outer pre_process slot; input validation +
# identifier screen)
#
# Node contract: extend FunctionNode; implement execute(self, state) -> dict —
# no `config` parameter. Return ONLY the fields this node changes (never full
# state). Read input_context via state.get("input_context", {}) — read-only.
# Never import from mediator/, api/, or other agents.
#
# Trust gate: this is the outer backbone gate slot — the manifest declares
# required_trust_level "VERIFIED_EXTERNAL" (config/agent.yaml), so this node
# gates external callers before the inner domain workflow runs.
#
# Identifier screen: direct identifiers a caller may paste into the question
# while describing their situation ("my staff number is ...", "mail me at ...")
# are redacted here, before validated_input is written — so a raw identifier
# never reaches the inner domain nodes, the checkpoint store, or the audit
# trail. The catalog this agent searches holds program descriptions only; no
# personnel record is ever read, so this screen is defence in depth on the
# caller's own free text.

import re
from typing import Any, ClassVar, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.progress import emit_progress
from src.services.failure_message import EMPTY_INPUT

# Surface-level identifier patterns redacted before validated_input is written.
_IDENTIFIER_PATTERNS: List[re.Pattern[str]] = [
    # Long numeric ID sequences: 10-19 consecutive digits, optionally grouped.
    # Guarded so a catalog identifier (TRN-2026-0014-01) is not clipped.
    re.compile(r"(?<![A-Za-z0-9-])\d{4}[- ]?\d{4}[- ]?\d{2,11}(?![A-Za-z0-9-])"),
    # National-ID shape.
    re.compile(r"(?<![A-Za-z0-9-])\d{3}-\d{2}-\d{4}(?![A-Za-z0-9-])"),
    # E-mail addresses.
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
]
_REDACTION = "[REDACTED]"

# Caller-supplied telemetry labels are locked to an inert identifier shape
# before they are written to state — free text in a caller-controlled label is
# a log and output injection vector.
_INERT_IDENTIFIER_RE = re.compile(r"^[a-z0-9_]{1,32}$")


def _strip_identifiers(text: str) -> str:
    """Redact direct-identifier tokens from a free-text string."""
    for pattern in _IDENTIFIER_PATTERNS:
        text = pattern.sub(_REDACTION, text)
    return text


def _inert_label(value: object) -> str:
    """Lock a caller-supplied label to an inert identifier, else 'unknown'.

    The rejected value is never echoed anywhere — it is simply replaced.
    """
    if isinstance(value, str) and _INERT_IDENTIFIER_RE.match(value):
        return value
    return "unknown"


class PreProcessNode(FunctionNode):
    """Validate and screen the incoming question before main processing.

    Rejects an empty or non-text request before the inner domain workflow
    runs, and redacts direct identifiers from the payload.
    """

    # Explicit by design, not inherited implicitly. Outer backbone gate slot —
    # matches the manifest's declared required_trust_level (config/agent.yaml).
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> dict[str, Any]:
        user_input = state.get("user_input", "")
        input_context = state.get("input_context", {})  # read-only

        if not user_input or not isinstance(user_input, str) or not user_input.strip():
            emit_progress(EMPTY_INPUT)
            # A value the caller can correct: nothing downstream runs, and the
            # run COMPLETES carrying the reason so the request can be sent again.
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": "EMPTY_INPUT",
                "error_log": ["PreProcessNode: user_input is empty or missing"],
            }

        validated_input = _strip_identifiers(user_input.strip())

        emit_trace_event(
            "pre_process_complete",
            {"input_chars": len(validated_input)},
            state,
        )

        channel = _inert_label(input_context.get("channel") if isinstance(input_context, dict) else None)

        return {
            "validated_input": validated_input,
            "enriched_context": {
                "source": "CorporateTrainingFAQAgent",
                "channel": channel,
            },
            "status": AgentStatus.SUCCESS.value,
        }
