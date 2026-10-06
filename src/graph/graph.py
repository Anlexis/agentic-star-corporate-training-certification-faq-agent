"""AgentCore Platform v1.0"""

# EDU-C2-014 — Outer graph (AgentBaseGraph; Cat 2 two-layer nested architecture)
#
# Corporate Training & Certification FAQ Agent.
#
# Architecture (Cat 2):
#
#   Outer backbone (fixed — identical to Cat 1, do NOT override add_edges()):
#     START -> initialize -> pre_process -> main -> {route} -> post_process
#           -> finalize -> END
#                             |  (RETRY, bounded by max_retry)
#                             -> pre_process
#
#   The `main` slot is a GraphNode subclass (TrainingFAQGraphNode) that
#   delegates the whole domain workflow to DomainWorkflowGraph (inner
#   BaseGraph: input_validate -> query_normalize -> kb_retrieve ->
#   response_generate -> output_validate).
#
#   Domain complexity is fully encapsulated inside the inner graph; the outer
#   backbone is never modified.
#
# Directory layout:
#   src/graph/graph.py                 <- outer graph (this file)
#   src/graph/domain_workflow_graph.py <- inner graph (multi-step topology)
#   src/graph/context_bridge.py        <- input_context hand-off outer -> inner
#
# Class-name contract:
#   graph.py class:           CorporateTrainingFAQAgent (this file)
#   config/agent.yaml class:  "src.graph.graph.CorporateTrainingFAQAgent"
#   src/api/server.py import: from src.graph.graph import CorporateTrainingFAQAgent
#
# Rules enforced:
#   - CorporateTrainingFAQAgent inherits AgentBaseGraph (framework base class —
#     direct framework inheritance)
#   - super().register_nodes() called first (fills initialize + finalize)
#   - TrainingFAQGraphNode assigned to self._nodes["main"]
#   - _parent_config() forwards the config/config.yaml retrieval/answer blocks
#     (never {})
#   - extract_input() bridges the caller's input_context to the inner graph
#     (src/graph/context_bridge.py)
#   - merge_output() returns only changed keys
#   - get_output() EXTENDS super().get_output() — the structured `programs`
#     list is surfaced ONLY on SUCCESS and is re-screened (fail-closed)
#   - add_edges() NOT overridden on the outer graph
#   - No platform-internal SDK imports

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.graph.context_bridge import set_caller_input_context
from src.nodes.output_validate import screen_value
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State, from_json

if TYPE_CHECKING:
    from src.graph.domain_workflow_graph import DomainWorkflowGraph

# Runtime-config path: src/graph/graph.py -> parents[2] = repo root.
# config/config.yaml holds the runtime parameters (max_retry, timeout_s) and
# the retrieval / answer tuning blocks; config/agent.yaml is the static
# manifest (identity, entry point, trust level) and carries no tuning values.
_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"

# Fallbacks mirror the `retrieval` / `answer` blocks in config/config.yaml so
# _parent_config() never forwards an empty config even if the file is
# unreadable in an exotic deployment layout.
_FALLBACK_RETRIEVAL: dict[str, Any] = {
    "top_k": 4,
    "score_threshold": 0.25,
    "kb_path": "config/kb/training_catalog_kb.json",
}
_FALLBACK_ANSWER: dict[str, Any] = {
    "max_answer_chars": 8000,
    "supported_languages": ["ja", "vi", "en"],
    "escalation_keywords": ["grievance", "waiver", "exception", "appeal"],
}


def runtime_config() -> dict[str, Any]:
    """Read the runtime parameters from config/config.yaml.

    Returns {} — never raises — when the file is absent, unreadable, not valid
    YAML, or not a mapping; callers apply their own fallbacks in that case.
    The static manifest (config/agent.yaml) is deliberately NOT read here: it
    declares identity and compile-time requirements only, so a reader pointed
    at it would silently return no tuning values at all.
    """
    try:
        import yaml

        loaded = yaml.safe_load(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return cast(dict[str, Any], loaded)


class TrainingFAQGraphNode(GraphNode):
    """GraphNode subclass assigned to the `main` slot of the outer agent.

    Wraps DomainWorkflowGraph (the inner Cat 2 pipeline). Called by the
    AgentBaseGraph backbone after pre_process and before post_process.

    Contracts:
      get_subgraph()  - instantiate DomainWorkflowGraph with the forwarded
                        runtime config (_parent_config())
      extract_input() - pull validated_input (identifier-screened) from the
                        outer state and stash input_context for the inner
                        graph (context bridge)
      merge_output()  - map sub_result fields into the outer state delta
                        (changed keys only)
      error_strategy  - "propagate": re-raise inner errors as SubgraphError
                        (fail-fast; the framework turns the raised error into
                        a terminal ERROR status on the outer state)
    """

    # "propagate": re-raise inner graph exceptions (default — fail fast).
    # "handle": call on_subgraph_error() instead — for graceful degradation.
    error_strategy: ClassVar[str] = "propagate"

    # False: this template does not use human-in-the-loop interrupts at all.
    propagate_hitl: ClassVar[bool] = False

    def _parent_config(self) -> dict[str, Any]:
        """Forward the `retrieval` + `answer` tuning blocks to the inner graph.

        Loads config/config.yaml (the runtime parameters file) and returns the
        two tuning blocks under config["configurable"] — never an empty dict.
        The inner graph republishes them into inner state
        (DomainWorkflowGraph._extra_initial_state()), which is how
        KBRetrieveNode, QueryNormalizeNode, ResponseGenerateNode and
        OutputValidateNode read live values: their execute() takes no `config`
        parameter, so a value that is not seeded into state would be dead
        configuration text.
        """
        runtime = runtime_config()
        retrieval = runtime.get("retrieval")
        if not isinstance(retrieval, dict) or not retrieval:
            retrieval = dict(_FALLBACK_RETRIEVAL)
        answer = runtime.get("answer")
        if not isinstance(answer, dict) or not answer:
            answer = dict(_FALLBACK_ANSWER)
        return {"configurable": {"retrieval": retrieval, "answer": answer}}

    def get_subgraph(self) -> "DomainWorkflowGraph":
        """Instantiate and return the inner domain workflow graph.

        DomainWorkflowGraph is imported lazily (inside the method) to avoid
        circular-import risk at module load time.

        The inner GRAPH receives the runtime config via its BaseGraph
        constructor — graph-level injection of immutable config, distinct from
        the per-node execute() contract. Its domain NODES still take no
        constructor arguments and read config exclusively from the
        state-seeded config fields.
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._parent_config())

    def execute(self, state: AgentState) -> dict[str, Any]:
        """Skip the inner graph when the request was already found unacceptable.

        A request declined by pre_process has no validated input to act on, so
        running the inner graph would only produce a second, vaguer reason for
        the same rejection - and overwrite the specific one already settled.
        """
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        result: dict[str, Any] = super().execute(state)
        return result

    def extract_input(self, state: AgentState) -> str:
        """Return the string input passed into inner_graph.invoke().

        PreProcessNode validates and identifier-screens the raw user_input and
        writes the result to validated_input; prefer that, falling back to
        user_input when validated_input is absent (unit tests).

        Also bridges the caller's input_context to the inner graph: the
        framework's GraphNode.execute() does not forward input_context on
        subgraph.invoke(), and extract_input is the last template-code hook
        that sees the outer state before the inner invoke — see
        src/graph/context_bridge.py. The bridged values are UNVALIDATED here;
        the inner InputValidateNode enforces the caller-data contract
        (fail-closed) before any downstream node reads them.
        """
        set_caller_input_context(state.get("input_context") or {})
        return cast(str, state.get("validated_input") or state.get("user_input", ""))

    def merge_output(self, state: AgentState, sub_result: dict[str, Any]) -> dict[str, Any]:
        """Map the inner graph's sub_result into the outer state delta.

        sub_result is the dict returned by DomainWorkflowGraph.get_output().
        Returns ONLY changed keys — never the full state.

        GraphNode.execute() raises before calling merge_output when the inner
        graph terminates with ERROR (error_strategy "propagate"), so this
        method only runs on a successful inner pass.

        `result` is what PostProcessNode (the outer output gate) reads and
        what the base envelope surfaces as `output`, so the rendered answer is
        mapped there as well as to the domain-named field.
        """
        return {
            # Outer reason wins: a reason settled before the inner run is the real
            # one, and a plain sub_result.get() would erase it.
            "error_code": state.get("error_code") or sub_result.get("error_code", ""),
            "training_faq_answer": sub_result.get("answer"),
            "result": sub_result.get("answer"),
            "eligibility_result": sub_result.get("eligibility_result"),
            "application_procedure": sub_result.get("application_procedure"),
            "deadline_info": sub_result.get("deadline_info"),
            "hr_escalation": sub_result.get("hr_escalation"),
            "out_of_scope": sub_result.get("out_of_scope"),
            "programs": sub_result.get("programs"),
            "status": sub_result.get("status"),
        }


class CorporateTrainingFAQAgent(AgentBaseGraph):
    """Outer graph for EDU-C2-014 (Cat 2, nested).

    Inherits AgentBaseGraph (framework base class) directly. Domain logic is
    fully encapsulated in TrainingFAQGraphNode (main slot), which delegates to
    DomainWorkflowGraph (inner BaseGraph).

    Backbone (fixed — identical to Cat 1):
        START -> initialize -> pre_process -> main -> post_process -> finalize
        -> END

    register_nodes() and get_output() are the ONLY overrides:
      - super().register_nodes() fills initialize and finalize
      - pre_process:  PreProcessNode (input validation + identifier screen)
      - main:         TrainingFAQGraphNode (delegates to DomainWorkflowGraph)
      - post_process: PostProcessNode (output screen)
      - get_output(): extends the base envelope with the structured
        `programs` list, ONLY on SUCCESS, re-screened fail-closed

    add_edges() is NOT overridden — backbone wiring belongs to the framework.
    """

    @property
    def name(self) -> str:
        """Agent identifier registered with the platform registry."""
        return "CorporateTrainingFAQAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        """Fill all 5 backbone slots.

        super().register_nodes() MUST be called first — it injects the
        framework's default initialize node (schema_version, session_id,
        trust_level) and finalize node (response metadata, total time).
        """
        super().register_nodes()  # fills: initialize, finalize

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = TrainingFAQGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.

    def get_output(self, state: AgentState) -> dict[str, Any]:
        """Extend the base envelope with the structured `programs` list.

        Which catalog programs an answer rests on is the product itself, not
        a side note, so the list is surfaced as a real (decoded) Python value
        on top of the base output/status/trace_id/correlation_id/node_history
        envelope.

        Fail-closed, twice over:
          1. `programs` is attached only when the terminal status is SUCCESS —
             a trust-gate denial, a blocked output or a subgraph error returns
             the base envelope alone.
          2. Even on SUCCESS the list is re-screened with the same
             implementation the two gate nodes use. PostProcessNode screens
             `result`; this pass covers the field get_output() adds on top of
             it. A violation flips the status to ERROR and withholds the list
             — never a partial payload.
        """
        base: dict[str, Any] = cast(dict[str, Any], super().get_output(state))
        # A run that completed WITHOUT answering holds the sentence saying what
        # to correct, not a catalog result: no programs were selected, so the
        # structured list does not apply and is not released.
        if state.get("error_code"):
            return base

        if state.get("status") != AgentStatus.SUCCESS.value:
            return base

        programs = from_json(state.get("programs"), []) or []

        if screen_value(programs):
            base["status"] = AgentStatus.ERROR.value
            return base

        base["programs"] = programs
        return base


# Back-compat alias — config/agent.yaml declares the dotted class path and
# src/api/server.py imports the class directly. Keep both names pointing at
# the agent.
Graph = CorporateTrainingFAQAgent
