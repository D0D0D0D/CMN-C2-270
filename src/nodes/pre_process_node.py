"""AgentCore Platform v1.0 — CMN-C2-270 pre_process (EmployeeProfilePreprocessNode)."""

# Outer backbone `pre_process` slot. Normalizes the employee profile (JSON) and
# builds the peer-trend search query. S-2 domain checks via _extra hook.

from __future__ import annotations

import json
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

_MAX_INPUT_CHARS = 20_000


class EmployeeProfilePreprocessNode(FunctionNode):
    """Validate & normalize the employee's self-service profile input."""

    # S-1: the employee is an authenticated self-service user (not anonymous).
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict) -> dict:
        """S-2 domain check (runs after the framework PII scan).

        Enforce an input size ceiling. The profile legitimately contains
        personal figures (age/income/assets); those are handled under the
        anonymization-by-design policy downstream and are not blocked here.
        """
        user_input = state.get("user_input", "") or ""
        if len(user_input) > _MAX_INPUT_CHARS:
            return {
                **state,
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    *(state.get("error_log") or []),
                    "EmployeeProfilePreprocessNode: input exceeds size limit",
                ],
            }
        return state

    def execute(self, state: dict) -> dict:
        user_input = state.get("user_input", "")
        if not user_input or not user_input.strip():
            emit_trace_event(
                "profile_validation_failed",
                {"reason": "empty_input"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["EmployeeProfilePreprocessNode: user_input is empty or missing"],
            }

        profile = self._parse_profile(user_input)
        query = self._build_query(profile)

        emit_trace_event(
            "profile_normalized",
            {"has_age": "current_age" in profile, "query_len": len(query)},
            state,
        )
        return {
            "validated_input": query,
            "employee_profile": profile,
            "status": AgentStatus.SUCCESS.value,
        }

    @staticmethod
    def _parse_profile(user_input: str) -> dict:
        """Accept either a JSON object or free text. JSON is preferred; free
        text is wrapped so downstream nodes still get a dict."""
        text = user_input.strip()
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        return {"free_text": text}

    @staticmethod
    def _build_query(profile: dict) -> str:
        """Build the peer-trend retrieval query from the anonymized segment
        keys (age band / job grade), never from raw identifiers."""
        age = profile.get("current_age")
        age_band = ""
        if isinstance(age, (int, float)):
            decade = int(age) // 10 * 10
            age_band = f"{decade}代"
        grade = profile.get("job_grade", "")
        goal = profile.get("goal", "資産形成")
        parts = [p for p in (age_band, str(grade), str(goal)) if p]
        return " ".join(parts) if parts else profile.get("free_text", "資産形成 傾向")
