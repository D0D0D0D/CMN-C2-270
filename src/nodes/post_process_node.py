"""AgentCore Platform v1.0 — CMN-C2-270 post_process (AdvisoryPostProcessNode)."""

# Outer backbone `post_process` slot. S-3 guard L3 (final independent re-scan,
# fail-closed) + L4 (non-solicitation disclaimer preservation). Formats output.

from __future__ import annotations

from typing import ClassVar

from framework.errors import SecurityViolationError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.status_util import is_error as _is_error
from src.services.product_guard import (
    DISCLAIMER,
    scan_for_product_recommendation,
)


class AdvisoryPostProcessNode(FunctionNode):
    """Final formatting + S-3 L3/L4 fail-closed product-recommendation gate."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_output(self, result: dict) -> dict:
        """S-3 L3 — final, independent re-scan of the outgoing text.

        Runs after the framework's @final credential scan. This is the last
        line of defense before the output leaves the agent: if any individual
        product recommendation survived L1/L2, reject the whole output
        (fail-closed) rather than filter-and-pass.
        """
        text = result.get("output", "")
        if isinstance(text, str) and text:
            violations = scan_for_product_recommendation(text)
            if violations:
                raise SecurityViolationError(
                    "S-3 L3: individual product recommendation detected in output: " + "; ".join(violations[:5])
                )
        return result

    def execute(self, state: dict) -> dict:
        # Pass through upstream failures (pre→main is an unconditional edge).
        if _is_error(state.get("status")):
            return {
                "output": "",
                "status": AgentStatus.ERROR.value,
            }

        advisory = state.get("advisory_output", "") or ""

        # L4 — always append the non-solicitation disclaimer (preservation).
        output = advisory.rstrip()
        if DISCLAIMER not in output:
            output = f"{output}\n\n{DISCLAIMER}" if output else DISCLAIMER

        emit_trace_event(
            "advisory_finalized",
            {"output_len": len(output), "disclaimer_present": DISCLAIMER in output},
            state,
        )
        return {
            "output": output,
            "status": AgentStatus.SUCCESS.value,
        }
