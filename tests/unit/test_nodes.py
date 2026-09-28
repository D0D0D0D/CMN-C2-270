# CMN-C2-270 — Unit tests: nodes (contract + behavior)

import inspect

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.lifeplan_projection_node import LifePlanProjectionNode
from src.nodes.peer_trend_retrieval_node import PeerTrendRetrievalNode
from src.nodes.post_process_node import AdvisoryPostProcessNode
from src.nodes.pre_process_node import EmployeeProfilePreprocessNode
from src.nodes.wellness_advisory_synthesis_node import WellnessAdvisorySynthesisNode
from src.services import llm_client, peer_trend_store

ALL_NODES = [
    EmployeeProfilePreprocessNode,
    LifePlanProjectionNode,
    PeerTrendRetrievalNode,
    WellnessAdvisorySynthesisNode,
    AdvisoryPostProcessNode,
]


def _status(result: dict):
    s = result.get("status")
    return s.value if isinstance(s, AgentStatus) else s


class TestNodeContract:
    """node contract: execute(self, state), no _invoke_impl, explicit trust."""

    @pytest.mark.parametrize("node_cls", ALL_NODES)
    def test_execute_signature(self, node_cls):
        sig = inspect.signature(node_cls.execute)
        params = list(sig.parameters.keys())
        assert params[1] == "state"
        assert "_invoke_impl" not in node_cls.__dict__

    @pytest.mark.parametrize("node_cls", ALL_NODES)
    def test_trust_level_explicit_and_valid(self, node_cls):
        tl = node_cls.required_trust_level
        assert tl in (TrustLevel.ANONYMOUS, TrustLevel.VERIFIED_EXTERNAL, TrustLevel.INTERNAL)

    @pytest.mark.parametrize("node_cls", ALL_NODES)
    def test_constructible_with_no_args(self, node_cls):
        assert node_cls() is not None


class TestStatusIsJsonSafe:
    """Regression (review High #2): nodes must write status as a JSON-safe string
    (AgentStatus.X.value), never the enum object — State is checkpointed."""

    def test_preprocess_status_is_str(self):
        r = EmployeeProfilePreprocessNode().execute({"user_input": '{"current_age":30}'})
        assert isinstance(r["status"], str)

    def test_synthesis_status_is_str(self):
        llm_client.set_llm(None)
        r = WellnessAdvisorySynthesisNode().execute({"plan_projection": {}, "peer_trends": [], "runtime_knobs": {}})
        assert isinstance(r["status"], str)

    def test_error_passthrough_accepts_string_status(self):
        """Downstream nodes must recognize a string-form ERROR status (e.g. from
        a resumed checkpoint), not only the enum."""
        from framework.schemas.agent_status import AgentStatus as _S
        r = LifePlanProjectionNode().execute({"status": _S.ERROR.value})
        assert r == {}


class TestPreProcess:
    def setup_method(self):
        self.node = EmployeeProfilePreprocessNode()

    def test_empty_input_errors(self):
        r = self.node.execute({"user_input": "  "})
        assert _status(r) == AgentStatus.ERROR.value

    def test_json_profile_parsed(self):
        r = self.node.execute({"user_input": '{"current_age": 35, "job_grade": "主任"}'})
        assert _status(r) == AgentStatus.SUCCESS.value
        assert r["employee_profile"]["current_age"] == 35
        assert "30代" in r["validated_input"]

    def test_free_text_wrapped(self):
        r = self.node.execute({"user_input": "老後が不安です"})
        assert _status(r) == AgentStatus.SUCCESS.value
        assert r["employee_profile"]["free_text"] == "老後が不安です"


class TestLifePlanProjectionNode:
    def test_decodes_payload_and_projects(self):
        import json
        node = LifePlanProjectionNode()
        payload = json.dumps({"profile": {"current_age": 30, "current_assets": 1_000_000,
                                          "annual_income": 5_000_000, "annual_expense": 4_000_000},
                              "knobs": {}})
        r = node.execute({"user_input": payload, "status": AgentStatus.PENDING.value})
        assert _status(r) == AgentStatus.SUCCESS.value
        assert r["plan_projection"]["trajectory"]

    def test_upstream_error_passthrough(self):
        node = LifePlanProjectionNode()
        r = node.execute({"status": AgentStatus.ERROR})
        assert r == {}


class TestPeerTrendRetrievalNode:
    def teardown_method(self):
        peer_trend_store.set_store(None)

    def test_k_gated_and_success_on_empty(self):
        class Store:
            def search(self, q, top_k=10):
                return [{"text": "t", "group_size": 2}]  # below k → dropped
        peer_trend_store.set_store(Store())
        node = PeerTrendRetrievalNode()
        r = node.execute({"employee_profile": {"current_age": 35}, "runtime_knobs": {"k_anonymity_min": 10}})
        assert _status(r) == AgentStatus.SUCCESS.value
        assert r["peer_trends"] == []

    def test_store_unavailable_emits_observability_event(self, monkeypatch):
        """Review advisory: a store outage must be observable so a
        prolonged outage is distinguishable from a genuinely empty result. The
        node emits `peer_trend_store_unavailable`, still returns success, and the
        event payload carries NO profile or secret data."""
        import src.nodes.peer_trend_retrieval_node as mod

        events: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            mod, "emit_trace_event", lambda et, payload, state: events.append((et, payload))
        )
        peer_trend_store.set_store(None)  # unavailable
        r = mod.PeerTrendRetrievalNode().execute(
            {"employee_profile": {"current_age": 35, "job_grade": "M2"}, "runtime_knobs": {}}
        )
        assert _status(r) == AgentStatus.SUCCESS.value
        assert r["peer_trends"] == []
        types = [et for et, _ in events]
        assert "peer_trend_store_unavailable" in types
        # Payloads must not leak the profile or any raw identifier.
        for _, payload in events:
            for k in payload:
                assert k not in ("employee_profile", "profile", "current_age", "job_grade")
            assert "SECRET" not in str(payload)

    def test_store_available_does_not_emit_unavailable(self, monkeypatch):
        """A store that answers (even if k-gate empties the result) must NOT emit
        the unavailable signal — that is the outage/empty distinction."""
        import src.nodes.peer_trend_retrieval_node as mod

        events: list[str] = []
        monkeypatch.setattr(
            mod, "emit_trace_event", lambda et, payload, state: events.append(et)
        )

        class Store:
            def search(self, q, top_k=10):
                return [{"text": "t", "group_size": 2}]  # below k → dropped, but store answered

        peer_trend_store.set_store(Store())
        r = mod.PeerTrendRetrievalNode().execute(
            {"employee_profile": {"current_age": 40}, "runtime_knobs": {"k_anonymity_min": 10}}
        )
        assert _status(r) == AgentStatus.SUCCESS.value
        assert "peer_trend_store_unavailable" not in events


class TestSynthesisGuard:
    def teardown_method(self):
        llm_client.set_llm(None)

    def test_clean_llm_passes(self):
        class GoodLLM:
            def complete(self, m): return {"content": "一般的な資産形成の考え方を検討しましょう。"}
        llm_client.set_llm(GoodLLM())
        node = WellnessAdvisorySynthesisNode()
        r = node.execute({"plan_projection": {"final_balance": 1}, "peer_trends": [], "runtime_knobs": {}})
        assert _status(r) == AgentStatus.SUCCESS.value
        assert r["advisory_output"]

    def test_product_recommendation_blocked(self):
        """L2 guard: an LLM that always recommends a product → ERROR (fail-closed)."""
        class BadLLM:
            def complete(self, m): return {"content": "eMAXIS がおすすめです。買うべきです。"}
        llm_client.set_llm(BadLLM())
        node = WellnessAdvisorySynthesisNode()
        r = node.execute({"plan_projection": {}, "peer_trends": [], "runtime_knobs": {"guard_regen_max": 1}})
        assert _status(r) == AgentStatus.ERROR.value

    def test_fallback_when_no_llm(self):
        llm_client.set_llm(None)
        node = WellnessAdvisorySynthesisNode()
        r = node.execute({"plan_projection": {}, "peer_trends": [], "runtime_knobs": {}})
        assert _status(r) == AgentStatus.SUCCESS.value
        assert r["advisory_output"]


class TestPostProcess:
    def test_disclaimer_appended(self):
        from src.services.product_guard import DISCLAIMER
        node = AdvisoryPostProcessNode()
        r = node.execute({"advisory_output": "一般的な考え方です。", "status": AgentStatus.SUCCESS.value})
        assert DISCLAIMER in r["output"]

    def test_upstream_error_passthrough(self):
        node = AdvisoryPostProcessNode()
        r = node.execute({"status": AgentStatus.ERROR})
        assert _status(r) == AgentStatus.ERROR.value

    def test_l3_gate_rejects_product_recommendation(self):
        """S-3 L3 (_extra_security_gate_output) is fail-closed."""
        from framework.errors import SecurityViolationError
        node = AdvisoryPostProcessNode()
        with pytest.raises(SecurityViolationError):
            node._extra_security_gate_output({"output": "楽天証券がおすすめです。"})

    def test_l3_gate_passes_clean_output(self):
        node = AdvisoryPostProcessNode()
        out = node._extra_security_gate_output({"output": "一般的な資産形成の考え方です。"})
        assert out["output"]
