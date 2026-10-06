"""AgentCore Platform v1.0"""

# EDU-C2-014 — PostProcessNode (outer post_process slot; the last gate before
# the answer leaves the agent)
#
# Node contract: extend FunctionNode; implement execute(self, state) -> dict —
# no `config` parameter. Return ONLY the fields this node changes (never full
# state). Never import from mediator/, api/, or other agents.
#
# The inner OutputValidateNode screens the answer as the inner pipeline
# finishes; this node screens the SAME text again at the outer boundary, on
# the value that is actually about to be surfaced as `output`. The two run on
# different representations of the answer at different layers, so a mapping
# mistake between them cannot smuggle text past the screen. Both call the one
# implementation in src/nodes/output_validate.py.
#
# Fail-closed: a violation drops the answer and terminates with ERROR — the
# offending text is never surfaced, and the error names the LOCATION that
# failed, never the matched value and never the blocked form.
#
# CONTAINMENT — why the violation branch does what it does:
#
#   The base envelope resolves the released text as
#       formatted_output or result
#   so suppressing the answer takes BOTH halves of the work:
#
#   1. `formatted_output` must be set to a NON-EMPTY value. An empty string is
#      falsy, so `"" or result` evaluates to `result` — writing "" does not
#      suppress the answer, it hands the answer over. The withheld notice
#      below is deliberately truthy for exactly that reason.
#   2. Every other field that can carry a representation of the answer must be
#      cleared in the same delta. `result` still holds the rendered answer at
#      this point (the main slot's merge_output put it there), and the domain
#      fields hold quoted catalog content, so an ERROR status alone leaves all
#      of them readable.
#
#   The returned message names the field that failed and nothing else. Quoting
#   the matched text would put it back into the very delta this node returns —
#   where the framework's own output scan reads it, raises, and discards the
#   clearing along with the rest of the delta.

import logging
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.output_validate import screen_text
from shared.utils.audit_logger import emit_trace_event
from src.services.failure_message import EMPTY_INPUT, INPUT_REJECTED, INVALID_VALUE, TOO_LONG

logger = logging.getLogger(__name__)

# The state field this gate screens — and the only location named in the
# caller-visible error.
_SCREENED_FIELD = "result"

# Truthy by design. See the CONTAINMENT note above: an empty formatted_output
# is falsy and re-activates the base envelope's fallback to `result`, which is
# the un-screened answer. This placeholder is what the caller receives instead,
# and it carries no domain content of any kind.
_WITHHELD_NOTICE = "[output withheld: the response did not pass the output screen]"

# Every state field that can carry a representation of the answer, cleared
# together on a violation. Derived from the two routes an answer takes to a
# caller: TrainingFAQGraphNode.merge_output(), which maps the inner rendered
# answer and its quoted catalog fields into the outer state, and
# CorporateTrainingFAQAgent.get_output(), which surfaces `programs` on top of
# the base envelope. `hr_escalation` and `out_of_scope` are advisory booleans
# with no answer content and are deliberately left alone.
# tests/proof_of_boundary/test_pb_output_gate.py pins this list against
# merge_output() so a field added there cannot quietly escape the clearing.
_OUTPUT_BEARING_FIELDS: tuple[str, ...] = (
    "result",
    "training_faq_answer",
    "answer",
    "eligibility_result",
    "application_procedure",
    "deadline_info",
    "programs",
)


# Reason code -> the sentence the caller reads. A code with no entry falls
# back to the generic one rather than leaking the code itself.
_DEGRADED_MESSAGES = {
    "EMPTY_INPUT": EMPTY_INPUT,
    "QUESTION_TOO_LONG": TOO_LONG,
    "INVALID_REQUEST": INVALID_VALUE,
}


class PostProcessNode(FunctionNode):
    """Format and release the final output, behind the output screen."""

    # Explicit by design, not inherited implicitly. Outer backbone gate slot —
    # matches the manifest's declared required_trust_level (config/agent.yaml).
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> dict[str, Any]:
        # A run declined upstream has nothing to format. Render the reason as
        # the caller-facing body and carry the marker onward.
        marker = state.get("error_code")
        if marker:
            message = _DEGRADED_MESSAGES.get(marker, INPUT_REJECTED)
            emit_trace_event("post_process_degraded", {"reason": marker}, state)
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": marker,
                "output": message,
                "formatted_output": message,
            }
        result = state.get(_SCREENED_FIELD) or ""
        if not isinstance(result, str):
            result = str(result)

        violation = screen_text(result)
        if violation:
            # The blocked form goes to the operator log and the audit trail,
            # which are not caller-visible surfaces; the returned delta names
            # the failing field only.
            logger.error("PostProcessNode: %s blocked — %s", _SCREENED_FIELD, violation)
            emit_trace_event("post_process_blocked", {"field": _SCREENED_FIELD, "violation": violation}, state)
            blocked: dict[str, Any] = {field: None for field in _OUTPUT_BEARING_FIELDS}
            blocked["formatted_output"] = _WITHHELD_NOTICE
            blocked["status"] = AgentStatus.ERROR.value
            blocked["error_log"] = [f"PostProcessNode: output blocked — {_SCREENED_FIELD} failed the output screen"]
            return blocked

        emit_trace_event("post_process_complete", {}, state)
        return {
            "formatted_output": result,
            "status": AgentStatus.SUCCESS.value,
        }
