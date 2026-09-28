# CMN-C2-270 — Unit tests: WellnessWorkflowGraphNode (GraphNode boundary)

import json

from framework.schemas.agent_status import AgentStatus
from src.graph.graph import _KNOB_KEYS, WellnessWorkflowGraphNode


class TestExtractInput:
    def test_envelope_contains_profile_and_filtered_knobs(self):
        cfg = {
            "k_anonymity_min": 20,
            "guard_regen_max": 3,
            "unrelated_key": "ignored",  # must NOT leak into the envelope
        }
        node = WellnessWorkflowGraphNode(config=cfg)
        state = {"employee_profile": {"current_age": 42}}
        env = json.loads(node.extract_input(state))
        assert env["profile"] == {"current_age": 42}
        assert env["knobs"]["k_anonymity_min"] == 20
        assert env["knobs"]["guard_regen_max"] == 3
        assert "unrelated_key" not in env["knobs"]
        # every knob key must be a member of the allowlist
        assert set(env["knobs"]).issubset(set(_KNOB_KEYS))

    def test_missing_profile_yields_empty(self):
        node = WellnessWorkflowGraphNode(config={})
        env = json.loads(node.extract_input({}))
        assert env["profile"] == {}
        assert env["knobs"] == {}

    def test_envelope_is_valid_json_for_non_ascii(self):
        node = WellnessWorkflowGraphNode(config={})
        env = node.extract_input({"employee_profile": {"job_grade": "主任"}})
        assert json.loads(env)["profile"]["job_grade"] == "主任"


class TestMergeOutput:
    def test_maps_success_fields_and_normalizes_status(self):
        node = WellnessWorkflowGraphNode(config={})
        sub = {
            "advisory_output": "一般的な考え方",
            "plan_projection": {"final_balance": 1},
            "peer_trends": [{"group_size": 30}],
            "status": AgentStatus.SUCCESS,  # enum in → string out
        }
        out = node.merge_output({}, sub)
        assert out["advisory_output"] == "一般的な考え方"
        assert out["peer_trends"] == [{"group_size": 30}]
        assert out["status"] == AgentStatus.SUCCESS.value
        assert isinstance(out["status"], str)
        assert "error_log" not in out  # no inner error → not threaded

    def test_threads_inner_error_log_without_duplication(self):
        node = WellnessWorkflowGraphNode(config={})
        sub = {"status": AgentStatus.ERROR.value, "error_log": ["inner guard failed"]}
        # outer state already has a prior error — reducer will append, so
        # merge_output must return ONLY the inner errors (not concatenated).
        out = node.merge_output({"error_log": ["prior"]}, sub)
        assert out["error_log"] == ["inner guard failed"]


class TestSubgraphCache:
    def test_get_subgraph_is_cached(self):
        """F-01: the compiled inner graph is built once and reused."""
        node = WellnessWorkflowGraphNode(config={})
        first = node.get_subgraph()
        second = node.get_subgraph()
        assert first is second
