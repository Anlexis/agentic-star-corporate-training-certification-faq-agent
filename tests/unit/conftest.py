# EDU-C2-014 — unit-test fixtures
#
# Mutes the domain audit emitter in every src.nodes module. The framework
# imports shared.security at load time, so sys.modules-stubbing shared.* would
# break collection — instead each node module's already-imported
# emit_trace_event reference is monkeypatched to a no-op. A test that asserts
# on the audit payload re-patches the same module attribute with its own spy
# (the later patch wins for that test).
#
# The framework's OWN lifecycle events are left untouched — they are
# fire-and-forget log lines and part of the behaviour under test.

import pytest

import src.nodes.input_validate
import src.nodes.kb_retrieve
import src.nodes.output_validate
import src.nodes.pre_process_node
import src.nodes.query_normalize
import src.nodes.response_generate

_AUDITED_NODE_MODULES = (
    src.nodes.input_validate,
    src.nodes.kb_retrieve,
    src.nodes.output_validate,
    src.nodes.pre_process_node,
    src.nodes.query_normalize,
    src.nodes.response_generate,
)


@pytest.fixture(autouse=True)
def mute_domain_audit(monkeypatch):
    """Silence the domain audit emitter in every node module."""
    for module in _AUDITED_NODE_MODULES:
        monkeypatch.setattr(module, "emit_trace_event", lambda *args, **kwargs: None)
