# CMN-C2-270 — Integration tests: full outer graph invoke (backbone + inner workflow)

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import WellnessAdvisoryGraph
from src.services import llm_client, peer_trend_store
from src.services.product_guard import DISCLAIMER


class _FakeStore:
    def __init__(self, hits):
        self._hits = hits

    def search(self, query, top_k=10):
        return self._hits


def _status(result):
    s = result.get("status")
    return s.value if isinstance(s, AgentStatus) else s


def _build_agent():
    agent = WellnessAdvisoryGraph(config={"k_anonymity_min": 10, "guard_regen_max": 2})
    agent.compile()
    return agent


def _ctx():
    return InvocationContext(session_id="it", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


def _profile():
    return json.dumps({
        "current_age": 35, "current_assets": 5_000_000, "annual_income": 6_000_000,
        "annual_expense": 4_000_000, "annual_pension": 1_800_000, "job_grade": "主任", "goal": "老後資金",
    }, ensure_ascii=False)


class TestFullInvoke:
    def teardown_method(self):
        llm_client.set_llm(None)
        peer_trend_store.set_store(None)

    def test_happy_path_success_with_disclaimer(self):
        llm_client.set_llm(None)  # deterministic fallback
        peer_trend_store.set_store(_FakeStore([
            {"text": "企業型DCを月2万円活用", "group_size": 25, "metadata": {"group_size": 25, "age_band": "30代", "job_grade": "主任"}},
            {"text": "少人数", "group_size": 3, "metadata": {"age_band": "30代", "job_grade": "部長"}},
        ]))
        r = _build_agent().invoke(_profile(), ctx=_ctx())
        assert _status(r) == AgentStatus.SUCCESS.value
        assert DISCLAIMER in (r.get("output") or "")
        # k-anonymity: group_size=3 dropped, only the 25-member row survives.
        assert len(r.get("peer_trends", [])) == 1
        # deterministic projection ran
        assert r.get("plan_projection", {}).get("trajectory")
        history = [h.get("node") if isinstance(h, dict) else h for h in r.get("node_history", [])]
        assert "WellnessWorkflowGraphNode" in history

    def test_product_recommending_llm_is_blocked_fail_closed(self):
        class BadLLM:
            def complete(self, m):
                return {"content": "eMAXIS Slim をおすすめします。今すぐ買うべきです。"}
        llm_client.set_llm(BadLLM())
        peer_trend_store.set_store(_FakeStore([{"text": "t", "group_size": 30, "metadata": {"group_size": 30}}]))
        r = _build_agent().invoke(_profile(), ctx=_ctx())
        # Guard blocks → error status, no output leaked.
        assert _status(r) == AgentStatus.ERROR.value
        assert r.get("error_code") == "product_recommendation_guard_failed"
        assert not r.get("output")

    def test_subgraph_llm_exception_projects_safe_error_code_to_caller(self):
        class ExplodingLLM:
            def complete(self, m):
                raise RuntimeError("PROVIDER_RAW_DIAGNOSTIC_270")

        llm_client.set_llm(ExplodingLLM())
        peer_trend_store.set_store(_FakeStore([{"text": "t", "group_size": 30, "metadata": {"group_size": 30}}]))
        r = _build_agent().invoke(_profile(), ctx=_ctx())

        assert _status(r) == AgentStatus.ERROR.value
        assert r.get("error_code") == "subgraph_failed"
        assert r.get("error_type") == "SubgraphError"
        assert "error_code" in r.keys()
        assert "PROVIDER_RAW_DIAGNOSTIC_270" not in json.dumps(r, ensure_ascii=False, default=str)

    def test_empty_input_errors_cleanly(self):
        llm_client.set_llm(None)
        peer_trend_store.set_store(_FakeStore([]))
        r = _build_agent().invoke("   ", ctx=_ctx())
        assert _status(r) == AgentStatus.ERROR.value

    def test_no_peer_trends_still_succeeds(self):
        llm_client.set_llm(None)
        peer_trend_store.set_store(_FakeStore([]))  # empty store → no trends
        r = _build_agent().invoke(_profile(), ctx=_ctx())
        assert _status(r) == AgentStatus.SUCCESS.value
        assert r.get("peer_trends") == []
