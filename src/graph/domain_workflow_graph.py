"""AgentCore Platform v1.0"""

# EDU-C2-014 — DomainWorkflowGraph (inner BaseGraph)
#
# The INNER graph of the Cat 2 two-layer nested architecture. It encapsulates
# the whole corporate training FAQ workflow:
#
#   START -> input_validate -> query_normalize -> kb_retrieve
#         -> response_generate -> output_validate -> END
#
# Called by TrainingFAQGraphNode.get_subgraph() (src/graph/graph.py).
# get_output() shapes the sub_result dict consumed by merge_output() there.
#
# Rules enforced:
#   - inherits BaseGraph (fully custom topology — no forced backbone)
#   - implements every BaseGraph abstract method
#   - register_nodes() does NOT call super() (it is abstract in BaseGraph)
#   - does NOT register initialize / finalize (outer backbone concerns)
#   - get_output() designed together with TrainingFAQGraphNode.merge_output()
#   - no platform-internal SDK imports

from typing import Any

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.graph.context_bridge import get_caller_input_context
from src.nodes.input_validate import InputValidateNode
from src.nodes.kb_retrieve import KBRetrieveNode
from src.nodes.output_validate import OutputValidateNode
from src.nodes.query_normalize import QueryNormalizeNode
from src.nodes.response_generate import ResponseGenerateNode
from src.schemas.state import State, to_json


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for EDU-C2-014.

    Inherits BaseGraph directly for a fully custom node topology.

    Pipeline (linear):
        START
          -> input_validate    (caller-data contract gate)
          -> query_normalize   (language resolution + normalization)
          -> kb_retrieve       (training-catalog retrieval)
          -> response_generate (answer assembly + eligibility verdict)
          -> output_validate   (output screen)
          -> END

    Every node is a FunctionNode subclass returning partial-dict state
    updates. initialize / finalize are outer backbone concerns and are not
    registered here.
    """

    # -- Identity --------------------------------------------------------------

    @property
    def name(self) -> str:
        """Unique identifier for this inner graph."""
        return "CorporateTrainingFAQWorkflow"

    @property
    def state_schema(self) -> type:
        """TypedDict shared across the inner and outer graph."""
        return State

    # -- Config validation -----------------------------------------------------

    def _validate_config(self) -> None:
        """Validate the inner graph config before compilation.

        The forwarded `retrieval` / `answer` blocks are advisory: every value
        has a module-level default in the consuming node, so an absent or
        partial block degrades to those defaults rather than failing the run.
        Malformed values are coerced (not trusted) in the consuming node.
        """
        return None

    # -- State seeding ---------------------------------------------------------

    def _extra_initial_state(self) -> dict[str, Any]:
        """Seed the inner state with the runtime config and the caller context.

        Two hand-offs happen here, both invisible to the outer backbone:

        1. `retrieval_config` / `answer_config`:
           TrainingFAQGraphNode._parent_config() forwards the
           config/config.yaml `retrieval` + `answer` blocks under
           config["configurable"]; this hook republishes them as JSON-string
           state fields (structured State fields travel as JSON strings for
           checkpoint msgpack safety). The domain nodes read those fields —
           their execute() takes no `config` parameter, so this is the only
           route by which a configured value actually governs a run.

        2. `input_context`: the framework's GraphNode.execute() does not
           forward input_context on subgraph.invoke();
           TrainingFAQGraphNode.extract_input() stashes it via the context
           bridge immediately before the inner invoke and this hook reads it
           back — see src/graph/context_bridge.py. Inner nodes keep their
           plain state["input_context"] reads.
        """
        configurable = (self.config or {}).get("configurable", {})
        retrieval = configurable.get("retrieval") or {}
        answer = configurable.get("answer") or {}
        return {
            "retrieval_config": to_json(retrieval),
            "answer_config": to_json(answer),
            "input_context": get_caller_input_context(),
        }

    # -- Node registration -----------------------------------------------------

    def register_nodes(self) -> None:
        """Register all 5 domain nodes.

        No super() call — BaseGraph.register_nodes() is abstract. Do NOT
        register initialize or finalize; those are outer backbone concerns.
        Every key registered here is referenced in add_edges().
        """
        self._nodes["input_validate"] = InputValidateNode()
        self._nodes["query_normalize"] = QueryNormalizeNode()
        self._nodes["kb_retrieve"] = KBRetrieveNode()
        self._nodes["response_generate"] = ResponseGenerateNode()
        self._nodes["output_validate"] = OutputValidateNode()

    # -- Edge wiring -----------------------------------------------------------

    def add_edges(self) -> None:
        """Wire the linear training FAQ topology.

        Each step passes its partial-dict output into the shared State. The
        topology is intentionally linear — no conditional branching between
        domain nodes — so route() is implemented to satisfy the abstract
        contract but add_conditional_edges() is not used. A mid-pipeline ERROR
        is handled by the framework's short-circuit: once a node sets ERROR,
        the remaining nodes skip their execute().
        """
        self._sg.add_edge(START, "input_validate")
        self._sg.add_edge("input_validate", "query_normalize")
        self._sg.add_edge("query_normalize", "kb_retrieve")
        self._sg.add_edge("kb_retrieve", "response_generate")
        self._sg.add_edge("response_generate", "output_validate")
        self._sg.add_edge("output_validate", END)

    # -- Routing ---------------------------------------------------------------

    def route(self, state: AgentState) -> str:
        """Conditional routing — required by the BaseGraph abstract contract.

        This topology uses no conditional edges, so the method is never called
        at runtime. It returns END on an error status so an unexpected call
        cannot re-enter a processing node.
        """
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return "output_validate"

    # -- Output shape ----------------------------------------------------------

    def get_output(self, state: AgentState) -> dict[str, Any]:
        """Shape the sub_result dict returned to the outer graph.

        Received by TrainingFAQGraphNode.merge_output() in src/graph/graph.py.
        Both methods are designed together to guarantee field-name
        consistency:

            inner get_output()  emits -> "answer", "eligibility_result",
                                         "application_procedure",
                                         "deadline_info", "hr_escalation",
                                         "out_of_scope", "programs", "status"
            outer merge_output() reads -> all of the above
        """
        return {
            # the reason must leave the subgraph or the outer graph cannot report it
            "error_code": state.get("error_code"),
            "answer": state.get("answer"),
            "eligibility_result": state.get("eligibility_result"),
            "application_procedure": state.get("application_procedure"),
            "deadline_info": state.get("deadline_info"),
            "hr_escalation": state.get("hr_escalation"),
            "out_of_scope": state.get("out_of_scope"),
            "programs": state.get("programs"),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
