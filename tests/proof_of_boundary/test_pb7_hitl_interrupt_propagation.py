# PB-7: Human-in-the-loop interrupt-propagation boundary
#
# PB-7 verifies that a human-in-the-loop interrupt raised inside the agent
# graph propagates ACROSS the backbone / subgraph boundary up to the invoking
# caller — so an external orchestrator can pause the run, collect a human
# decision, and resume it. That behaviour is only meaningful for templates
# that opt into cross-boundary propagation: a main-slot GraphNode declaring
# `propagate_hitl = True`, or an explicit interrupt() checkpoint on the 5-node
# backbone.
#
# This template does NOT enable cross-boundary interrupt propagation. Its
# escalation signal is an advisory flag on a delivered answer
# (tests/proof_of_boundary/test_pb_escalation_flag.py), not a suspended run,
# and the backbone completes with no interrupt() checkpoint. There is
# therefore no propagation behaviour to assert, so PB-7 ships as a conditional
# skip — a real, importable module that skips with a clear reason (never
# `assert True`), ready to be filled in if propagation is ever wired.

import importlib

import pytest


def _hitl_propagation_enabled() -> bool:
    """True iff this template opts into cross-boundary interrupt propagation.

    Detected by inspecting the classes DEFINED in src/graph/graph.py for a
    node/graph subclass declaring ``propagate_hitl = True``. Imported
    defensively so collection never errors when the module is unavailable —
    PB-7 then simply skips.
    """
    try:
        graph_mod = importlib.import_module("src.graph.graph")
    except Exception:
        return False
    for obj in vars(graph_mod).values():
        if (
            isinstance(obj, type)
            and getattr(obj, "__module__", None) == graph_mod.__name__
            and getattr(obj, "propagate_hitl", False) is True
        ):
            return True
    return False


_HITL_PROPAGATION_ENABLED = _hitl_propagation_enabled()

_PB7_SKIP_REASON = (
    "cross-boundary interrupt propagation is not enabled for this template "
    "(no graph class declares propagate_hitl=True; no backbone interrupt() "
    "checkpoint) — PB-7 conditional skip"
)


@pytest.mark.skipif(not _HITL_PROPAGATION_ENABLED, reason=_PB7_SKIP_REASON)
class TestPB7HitlInterruptPropagation:
    """PB-7: an interrupt must propagate across the graph boundary.

    Skipped for this template — there is no propagation behaviour to verify.
    The real assertion belongs here once propagation is wired end to end.
    """

    def test_hitl_interrupt_propagates_to_caller(self):
        # Reached only when a graph class declares propagate_hitl=True. The
        # real assertion (invoke -> the interrupt surfaces to the caller ->
        # resume) is implemented at that point.
        raise AssertionError("PB-7 assertion not implemented — this template does not propagate interrupts")
