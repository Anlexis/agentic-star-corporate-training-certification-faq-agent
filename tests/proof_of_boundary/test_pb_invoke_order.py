# PB-6: Invoke execution order
#
# A full agent.invoke() must execute the fixed AgentBaseGraph backbone in
# order. The backbone is framework-owned and is never overridden by this
# template (add_edges() belongs to the framework):
#
#     START -> initialize -> pre_process -> main -> {route} -> post_process
#           -> finalize -> END
#
# The framework records every executed node in `node_history` (an AgentState
# field whose reducer accumulates entries in execution order); each entry is
# the node's CLASS NAME, appended by the framework's node wrapper.
#
# For EDU-C2-014 (Cat 2, two-layer nested) the `main` slot is a GraphNode
# subclass (TrainingFAQGraphNode) delegating to the inner DomainWorkflowGraph.
# The inner graph runs with its own state and its inner node_history is not
# merged back, so the OUTER node_history contains exactly the five backbone
# slots — never the inner domain nodes.
#
# A SUCCESS terminal status is required: on any non-SUCCESS status route()
# short-circuits main -> finalize and the post_process (output gate) slot is
# skipped, which is itself an invoke-order violation this test catches.
#
# Trust context: the manifest's declared caller level, VERIFIED_EXTERNAL. An
# internal context is never used here — it would over-privilege the run and
# hide a regression on the outer trust gate.
#
# Deterministic — no model call, no network. framework.* / src.* imports only.

import json
import pathlib

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import Graph

# --- TEMPLATE-SPECIFIC ------------------------------------------------------
# The `main`-slot GraphNode class name for THIS template. The other four
# backbone slot names are framework-fixed and identical across templates.
_MAIN_SLOT_NODE = "TrainingFAQGraphNode"

# The valid, identifier-free domain payload that drives the full workflow to a
# SUCCESS terminal status. MUST stay byte-equal to the `input` field of
# deploy/invoke_payload.json (the standard deployment payload) — enforced by
# test_payload_matches_deploy_invoke_payload below.
_VALID_PAYLOAD = "which leadership programs can a team lead with three years of service apply for?"

_DEPLOY_PAYLOAD_PATH = pathlib.Path(__file__).resolve().parents[2] / "deploy" / "invoke_payload.json"
# --- END TEMPLATE-SPECIFIC --------------------------------------------------

_EXPECTED_ORDER = [
    "InitializeNode",  # framework default  (initialize slot)
    "PreProcessNode",  # template-standard  (pre_process slot, input gate)
    _MAIN_SLOT_NODE,  # TEMPLATE-SPECIFIC  (main slot GraphNode)
    "PostProcessNode",  # template-standard  (post_process slot, output gate)
    "FinalizeNode",  # framework default  (finalize slot)
]


def _run() -> dict:
    """Run a full end-to-end invocation at the manifest's declared trust level."""
    ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL, caller_id="pb6-suite")
    return Graph().invoke(_VALID_PAYLOAD, ctx=ctx)


class TestInvokeOrderBoundary:
    def test_payload_matches_deploy_invoke_payload(self):
        """PB-6 proves invoke-order for the SAME payload the deployment
        smoke-check uses."""
        deployed = json.loads(_DEPLOY_PAYLOAD_PATH.read_text(encoding="utf-8"))
        assert _VALID_PAYLOAD == deployed["input"]

    def test_invoke_reaches_success(self):
        """The full run must terminate SUCCESS — otherwise route()
        short-circuits main -> finalize and the output gate never runs."""
        result = _run()
        assert result.get("status") == AgentStatus.SUCCESS.value, f"got {result.get('status')!r}: {result!r}"

    def test_output_is_non_empty(self):
        assert _run().get("output"), "invoke() surfaced an empty output"

    def test_node_history_is_populated(self):
        history = _run().get("node_history")
        assert isinstance(history, list) and history
        assert all(isinstance(name, str) for name in history)

    def test_backbone_slot_order(self):
        """The pre_process gate runs before the domain main slot, which runs
        before the post_process gate — as a strict ordered subsequence."""
        history = _run().get("node_history", [])
        ordered_slots = ["PreProcessNode", _MAIN_SLOT_NODE, "PostProcessNode"]
        for name in ordered_slots:
            assert name in history, f"expected {name!r} in node_history, got {history!r}"
        positions = [history.index(name) for name in ordered_slots]
        assert positions == sorted(positions), f"slots out of order: {ordered_slots} at {positions}"

    def test_full_backbone_sequence(self):
        history = _run().get("node_history", [])
        assert history == _EXPECTED_ORDER, f"expected {_EXPECTED_ORDER}, got {history}"
