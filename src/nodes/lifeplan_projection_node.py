"""AgentCore Platform v1.0 — CMN-C2-270 LifePlanProjectionNode (inner)."""

# Inner domain-workflow node (first step). Runs the deterministic life-plan
# projection. No public-simulator delegation (see docs/02_design.md 変更履歴).

from __future__ import annotations

import json
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.status_util import is_error as _is_error
from src.services.lifeplan_calc import estimate_annual_pension, project_life_plan


class LifePlanProjectionNode(FunctionNode):
    """Deterministic asset-formation & pension projection to projection_end_age.

    First node of the inner graph. GraphNode passes only `user_input` (a JSON
    string built by the outer WellnessWorkflowGraphNode.extract_input) into the
    inner graph — inner nodes do NOT receive the outer input_context. So this
    node decodes the payload and seeds `employee_profile` / `runtime_knobs`
    into the inner state for the downstream inner nodes.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict) -> dict:
        # Upstream error pass-through (inner graph is linear; keep failing state).
        if _is_error(state.get("status")):
            return {}

        payload = self._decode_payload(state.get("user_input", ""))
        profile = dict(payload.get("profile", {}) or {})
        knobs = dict(payload.get("knobs", {}) or {})
        config = dict(knobs.get("calc_config", {}) or {})

        # Fill pension from a formula estimate only when asked to; default mode
        # expects the certificate-input value already present in the profile.
        mode = config.get("pension_estimation_mode", "certificate_input")
        if mode == "formula_estimate" and "annual_pension" not in profile:
            profile["annual_pension"] = estimate_annual_pension(profile)

        projection = project_life_plan(profile, config)

        emit_trace_event(
            "life_plan_projected",
            {
                "final_balance": projection["final_balance"],
                "depletion_age": projection["depletion_age"],
                "years": len(projection["trajectory"]),
            },
            state,
        )
        return {
            "employee_profile": profile,
            "runtime_knobs": knobs,
            "plan_projection": projection,
            "status": AgentStatus.SUCCESS.value,
        }

    @staticmethod
    def _decode_payload(user_input: str) -> dict:
        """Decode the JSON payload produced by extract_input(); fall back to an
        empty profile if the string is not the expected JSON envelope."""
        try:
            data = json.loads(user_input)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
        return {"profile": {}, "knobs": {}}
