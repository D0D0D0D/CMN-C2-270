"""AgentCore Platform v1.0 — CMN-C2-270 outer graph (Employee Financial Wellness).

Cat 2: outer AgentBaseGraph fixed 5-node backbone with a GraphNode in the `main`
slot wrapping the inner WellnessWorkflowGraph (BaseGraph). Do NOT override
add_edges() — backbone wiring belongs to the framework.
"""

from __future__ import annotations

import json
from typing import ClassVar

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_status import AgentStatus
from src.nodes.post_process_node import AdvisoryPostProcessNode
from src.nodes.pre_process_node import EmployeeProfilePreprocessNode
from src.schemas.state import State
from src.services.status_util import status_value

# Per-request runtime knobs are read from outer config with these defaults.
_KNOB_KEYS = (
    "k_anonymity_min",
    "peer_top_k",
    "guard_regen_max",
    "product_name_blocklist",
    "calc_config",
)


class WellnessWorkflowGraphNode(GraphNode):
    """`main` slot — wraps the inner WellnessWorkflowGraph domain workflow."""

    # Handle inner errors with a safe projection. The framework's propagate path
    # serializes SubgraphError.__str__ into error_log, and that string embeds the
    # inner error_log verbatim.
    error_strategy: ClassVar[str] = "handle"
    propagate_hitl: ClassVar[bool] = False
    known_failure_codes: ClassVar[tuple[str, ...]] = ("product_recommendation_guard_failed",)

    def __init__(self, config: dict | None = None):
        super().__init__()
        self._config = config or {}
        self._subgraph = None  # cached compiled inner graph (see get_subgraph)

    def get_subgraph(self):
        """Instantiate + compile the inner graph once and cache it.

        GraphNode.execute() calls get_subgraph() on every invocation; rebuilding
        and re-compiling (LangGraph DAG validation) each time is wasteful. Cache
        the compiled instance in self._subgraph. Config flows via the inner graph
        constructor (BaseGraph.__init__(config)) — the wheel's GraphNode has no
        _parent_config(). This agent has no HITL, so a single shared inner graph
        is safe (no per-thread checkpointer to preserve).
        """
        if self._subgraph is None:
            from src.graph.domain_workflow_graph import WellnessWorkflowGraph

            inner = WellnessWorkflowGraph(config=self._config)
            inner.compile()
            self._subgraph = inner
        return self._subgraph

    def extract_input(self, state) -> str:
        """Serialize the profile + runtime knobs into a JSON envelope.

        GraphNode passes only a string into the inner graph, so we pack the
        structured payload here and the inner LifePlanProjectionNode decodes it.
        """
        knobs = {k: self._config[k] for k in _KNOB_KEYS if k in self._config}
        payload = {
            "profile": state.get("employee_profile", {}),
            "knobs": knobs,
        }
        return json.dumps(payload, ensure_ascii=False, default=str)

    def merge_output(self, state, sub_result: dict) -> dict:
        """Map inner sub_result back into the outer state (only changed keys).

        Thread the inner error_log through so a guard/S-3 failure inside the
        inner graph remains visible to the backbone for audit/compliance (do
        not silently drop it — get_output() exposes it deliberately). status is
        normalized to its string value for JSON/checkpoint safety.
        """
        merged = {
            "advisory_output": sub_result.get("advisory_output", ""),
            "plan_projection": sub_result.get("plan_projection", {}),
            "peer_trends": sub_result.get("peer_trends", []),
            "status": status_value(sub_result.get("status")),
        }
        # error_log uses an operator.add reducer — return ONLY the inner errors
        # (they are appended by the reducer; concatenating with state would dup).
        inner_errors = sub_result.get("error_log") or []
        if inner_errors:
            merged["error_log"] = list(inner_errors)
        if sub_result.get("error_code"):
            merged["error_code"] = sub_result["error_code"]
        if sub_result.get("error_type"):
            merged["error_type"] = sub_result["error_type"]
        return merged

    def _subgraph_error_code(self, error: Exception) -> str:
        """Return a safe public code for a subgraph failure."""
        generic_code = "subgraph_failed"
        if not self.known_failure_codes or isinstance(self.known_failure_codes, str):
            return generic_code
        inner_error_log = getattr(error, "error_log", None)
        if not isinstance(inner_error_log, (list, tuple)):
            return generic_code
        for entry in inner_error_log:
            if not isinstance(entry, str):
                continue
            for code in self.known_failure_codes:
                if code and code in entry:
                    return code
        return generic_code

    def on_subgraph_error(self, state, error: Exception) -> dict:
        code = self._subgraph_error_code(error)
        return {
            "status": AgentStatus.ERROR.value,
            "error_code": code,
            "error_type": type(error).__name__,
            "error_log": [f"WellnessWorkflowGraphNode: {code}"],
        }


class WellnessAdvisoryGraph(AgentBaseGraph):
    """Outer graph: Employee Financial Wellness Self-Advisory Agent."""

    @property
    def name(self) -> str:
        return "cmn_c2_270"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects initialize + finalize
        self._nodes["pre_process"] = EmployeeProfilePreprocessNode()
        self._nodes["main"] = WellnessWorkflowGraphNode(config=self.config)
        self._nodes["post_process"] = AdvisoryPostProcessNode()

    # add_edges() intentionally NOT overridden — fixed backbone.

    def get_output(self, state) -> dict:
        """Expose our `output` key (post_process writes `output`, not the
        framework default `formatted_output`/`result`)."""
        return {
            "output": state.get("output"),
            "advisory_output": state.get("advisory_output", ""),
            "plan_projection": state.get("plan_projection", {}),
            "peer_trends": state.get("peer_trends", []),
            "status": status_value(state.get("status")),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
            "error_code": state.get("error_code"),
            "error_type": state.get("error_type"),
            "error_log": list(dict.fromkeys(state.get("error_log", []))),
        }
