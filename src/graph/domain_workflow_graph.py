"""AgentCore Platform v1.0 — CMN-C2-270 inner domain workflow graph.

Inner graph for the Cat 2 template. Inherits BaseGraph directly (NOT
AgentBaseGraph) so that AgentState / InvocationContext / the S-gates are managed
once by the outer backbone and are not double-executed inside the inner loop
(review F-01). Fully custom linear topology:

    START → life_plan_projection → peer_trend_retrieval
          → wellness_advisory_synthesis → END

Instantiated by WellnessWorkflowGraphNode.get_subgraph() in graph.py.
"""

from __future__ import annotations

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_status import AgentStatus
from src.nodes.lifeplan_projection_node import LifePlanProjectionNode
from src.nodes.peer_trend_retrieval_node import PeerTrendRetrievalNode
from src.nodes.wellness_advisory_synthesis_node import WellnessAdvisorySynthesisNode
from src.schemas.state import State


class WellnessWorkflowGraph(BaseGraph):
    """Multi-step domain workflow: projection → peer trends → synthesis."""

    @property
    def name(self) -> str:
        return "wellness_advisory_workflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        # No mandatory inner config; LLM/vector-store are injected via seams.
        pass

    def register_nodes(self) -> None:
        # No super() — BaseGraph.register_nodes() is abstract. Do NOT register
        # initialize / finalize (outer backbone concern).
        self._nodes["life_plan_projection"] = LifePlanProjectionNode()
        self._nodes["peer_trend_retrieval"] = PeerTrendRetrievalNode()
        self._nodes["wellness_advisory_synthesis"] = WellnessAdvisorySynthesisNode()

    def add_edges(self) -> None:
        self._sg.add_edge(START, "life_plan_projection")
        self._sg.add_edge("life_plan_projection", "peer_trend_retrieval")
        self._sg.add_edge("peer_trend_retrieval", "wellness_advisory_synthesis")
        self._sg.add_edge("wellness_advisory_synthesis", END)

    def route(self, state) -> str:
        # Linear topology — route() is required by the ABC but not wired via
        # add_conditional_edges(). Provide meaningful status logic regardless.
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return "wellness_advisory_synthesis"

    def get_output(self, state) -> dict:
        """Shape sub_result for the outer WellnessWorkflowGraphNode.merge_output()."""
        return {
            "advisory_output": state.get("advisory_output", ""),
            "plan_projection": state.get("plan_projection", {}),
            "peer_trends": state.get("peer_trends", []),
            "status": state.get("status"),
            "error_code": state.get("error_code"),
            "error_type": state.get("error_type"),
            "error_log": state.get("error_log", []),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
