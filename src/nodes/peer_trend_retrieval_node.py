"""AgentCore Platform v1.0 — CMN-C2-270 PeerTrendRetrievalNode (inner)."""

# Inner domain-workflow node (second step). Vector search over the company's
# anonymized peer-trend store, k-anonymity gated (moat + safety).

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.status_util import is_error as _is_error
from src.services.peer_trend_store import DEFAULT_K_ANONYMITY_MIN, retrieve_peer_trends


class PeerTrendRetrievalNode(FunctionNode):
    """Retrieve same-age-band / same-grade anonymized peer trends (k-anonymous)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict) -> dict:
        if _is_error(state.get("status")):
            return {}

        knobs = state.get("runtime_knobs", {}) if isinstance(state.get("runtime_knobs"), dict) else {}
        profile = state.get("employee_profile", {}) if isinstance(state.get("employee_profile"), dict) else {}
        query = self._build_query(profile)
        k_min = int(knobs.get("k_anonymity_min", DEFAULT_K_ANONYMITY_MIN))
        top_k = int(knobs.get("peer_top_k", 10))

        trends, store_unavailable = retrieve_peer_trends(query, top_k=top_k, k_anonymity_min=k_min)

        emit_trace_event(
            "peer_trends_retrieved",
            # store_unavailable distinguishes a store outage (not configured /
            # unreachable) from a genuinely empty k-anonymous result, so a
            # prolonged outage is observable (review advisory). Payload
            # carries only counts/flags — never profile or secret data.
            {
                "k_anonymity_min": k_min,
                "returned": len(trends),
                "store_unavailable": store_unavailable,
            },
            state,
        )
        if store_unavailable:
            # Non-fatal: emit a distinct signal so ops can alert on a prolonged
            # store outage without mistaking it for "no matching segment".
            emit_trace_event(
                "peer_trend_store_unavailable",
                {"query_len": len(query)},
                state,
            )
        # Empty is a valid outcome (no segment met k, or store unavailable) —
        # not an error; the synthesis node degrades to projection-only advice.
        return {
            "peer_trends": trends,
            "status": AgentStatus.SUCCESS.value,
        }

    @staticmethod
    def _build_query(profile: dict) -> str:
        """Build the retrieval query from anonymized segment keys (age band /
        job grade) — never from raw identifiers."""
        age = profile.get("current_age")
        age_band = ""
        if isinstance(age, (int, float)):
            age_band = f"{int(age) // 10 * 10}代"
        grade = profile.get("job_grade", "")
        goal = profile.get("goal", "資産形成")
        parts = [p for p in (age_band, str(grade), str(goal)) if p]
        return " ".join(parts) if parts else "資産形成 傾向"
