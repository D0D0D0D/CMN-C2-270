"""AgentCore Platform v1.0 — CMN-C2-270 State schema."""

# ADR-005: State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or Pydantic models.

from __future__ import annotations

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Employee Financial Wellness Self-Advisory Agent state.

    Flat TypedDict (primitives + JSON-serializable only). All shared fields
    (user_input, status, session_id, node_history, error_log, hitl_*, etc.)
    are inherited from AgentState.

    No credentials / PII raw values / Pydantic objects are stored here — State
    is checkpointed to the DB, so anything here lands in plaintext.
    """

    # Normalized employee profile (JSON-safe dict) produced by pre_process.
    validated_input: str
    employee_profile: dict

    # Per-request runtime knobs (k_anonymity_min, calc_config, guard_regen_max,
    # product_name_blocklist, ...) threaded into the inner graph via the
    # extract_input JSON envelope. JSON-safe primitives only.
    runtime_knobs: dict

    # Deterministic life-plan projection result (asset balance trajectory,
    # pension estimate) — numbers only, produced by LifePlanProjectionNode.
    plan_projection: dict

    # Anonymized in-company peer trends (k-anonymity enforced; empty when the
    # matching segment is below k) produced by PeerTrendRetrievalNode.
    peer_trends: list

    # Synthesized "material to think with" for the employee (no individual
    # product recommendation) produced by WellnessAdvisorySynthesisNode.
    advisory_output: str

    # Final formatted output (disclaimer appended) produced by post_process.
    output: str

    # Error projection channels. LangGraph keeps only keys declared on the
    # TypedDict schema, so subgraph error codes must be declared here to reach
    # the outer caller.
    error_code: str
    error_type: str
