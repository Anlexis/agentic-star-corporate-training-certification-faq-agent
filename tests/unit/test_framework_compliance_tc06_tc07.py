"""TC-06 / TC-07: the framework security gates must not be bypassable.

These framework-compliance tests are required for every template that has one
or more FunctionNode subclasses. They validate the runtime enforcement
boundary: domain nodes may extend the input/output gates only through
_extra_security_gate_input() and _extra_security_gate_output(), never by
replacing the default gates.
"""

import pytest

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel


class TestFunctionNodeFinalSecurityGates:
    """TC-06/TC-07: the default gates are non-bypassable."""

    def test_tc06_input_gate_cannot_be_overridden(self):
        """TC-06: overriding the default input gate raises at class definition."""
        with pytest.raises(TypeError, match="_security_gate_input"):

            class _InvalidInputGateOverride(FunctionNode):
                required_trust_level = TrustLevel.ANONYMOUS

                def _security_gate_input(self, state):
                    return state

                def execute(self, state):
                    return {"status": AgentStatus.SUCCESS.value}

    def test_tc07_output_gate_cannot_be_overridden(self):
        """TC-07: overriding the default output gate raises at class definition."""
        with pytest.raises(TypeError, match="_security_gate_output"):

            class _InvalidOutputGateOverride(FunctionNode):
                required_trust_level = TrustLevel.ANONYMOUS

                def _security_gate_output(self, result):
                    return result

                def execute(self, state):
                    return {"status": AgentStatus.SUCCESS.value}

    def test_every_domain_node_declares_its_trust_level(self):
        """A node that inherits the permissive default implicitly is rejected
        at class-definition time; assert every shipped node declares one."""
        from src.nodes.input_validate import InputValidateNode
        from src.nodes.kb_retrieve import KBRetrieveNode
        from src.nodes.output_validate import OutputValidateNode
        from src.nodes.post_process_node import PostProcessNode
        from src.nodes.pre_process_node import PreProcessNode
        from src.nodes.query_normalize import QueryNormalizeNode
        from src.nodes.response_generate import ResponseGenerateNode

        for node_class in (
            InputValidateNode,
            KBRetrieveNode,
            OutputValidateNode,
            PostProcessNode,
            PreProcessNode,
            QueryNormalizeNode,
            ResponseGenerateNode,
        ):
            assert "required_trust_level" in node_class.__dict__, node_class.__name__

    def test_backbone_gate_slots_match_the_declared_caller_level(self):
        """The manifest declares VERIFIED_EXTERNAL; the two outer backbone gate
        slots must require exactly that, so an anonymous caller cannot reach
        the domain workflow."""
        from src.nodes.post_process_node import PostProcessNode
        from src.nodes.pre_process_node import PreProcessNode

        assert PreProcessNode.required_trust_level is TrustLevel.VERIFIED_EXTERNAL
        assert PostProcessNode.required_trust_level is TrustLevel.VERIFIED_EXTERNAL
