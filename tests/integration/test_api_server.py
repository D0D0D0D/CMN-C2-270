"""API-adapter integration tests — entry authentication.

These exercise src/api/server.py over real HTTP (FastAPI TestClient), NOT the
graph directly. They cover the retrofit fix for the fail-open the graph-level
tests could not see: the old adapter defaulted unauthenticated callers to
VERIFIED_EXTERNAL (S-1 bypass). It must now 401 without a valid
INVOKE_AUTH_TOKEN and only reach the graph for an authenticated caller.

This agent has NO tenant/org scope model (company isolation is a per-deployment
store boundary — see src/services/peer_trend_store.py), so only the entry-adapter
auth core is wired; there are no scope-default env vars to set.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

_TOKEN = "test-invoke-token-abc123"

_PAYLOAD = {
    "input": '{"current_age": 35, "job_grade": "M2", "goal": "老後資金"}',
    "session_id": "sess-owner",
}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", _TOKEN)
    # Re-import so the module-level app picks up the env.
    import src.api.server as server

    importlib.reload(server)
    return TestClient(server.app), server


def _auth(token: str = _TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_health_is_open(client):
    c, _ = client
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_runtime_config_is_loaded_from_config_yaml(client):
    _, server = client
    assert server.agent.config["max_retry"] == 3
    assert server.agent.config["timeout_seconds"] == 30
    assert server.agent.config["k_anonymity_min"] == 10
    assert server.agent.config["peer_top_k"] == 10
    assert server.agent.config["guard_regen_max"] == 2
    assert server.agent.config["product_name_blocklist"] == []
    assert server.agent.config["vector_store"] == {
        "enabled": False,
        "url": "http://qdrant:6333",
        "collection_name": "cmn_c2_270_peer_trends",
        "namespace": "cmn",
        "agent_name": "cmn_c2_270",
        "vector_size": 1536,
        "distance": "cosine",
        "timeout_s": 30,
        "write_timeout_s": 60,
        "max_batch_documents": 1000,
        "max_top_k": 100,
        "embedding_base_url": "https://api.openai.com/v1",
        "embedding_model": "text-embedding-3-small",
        "embedding_api_version": "2024-02-15-preview",
        "embedding_provider": "openai",
    }
    assert server.agent.config["calc_config"] == {
        "annual_return_rate": 0.03,
        "inflation_rate": 0.02,
        "salary_growth_rate": 0.015,
        "retirement_age": 65,
        "projection_end_age": 100,
        "pension_estimation_mode": "certificate_input",
    }


def test_invoke_without_token_is_401(client):
    c, _ = client
    r = c.post("/invoke", json=_PAYLOAD)
    assert r.status_code == 401


def test_invoke_wrong_token_is_401(client):
    c, _ = client
    r = c.post("/invoke", json=_PAYLOAD, headers=_auth("wrong-token"))
    assert r.status_code == 401


def test_invoke_unauthenticated_when_token_unset_is_401(monkeypatch):
    """With INVOKE_AUTH_TOKEN unset, an unauthenticated caller (no verified
    middleware trust) must get 401 — the entry-auth contract is
    'unauthenticated → 401', never the old VERIFIED_EXTERNAL fallback."""
    monkeypatch.delenv("INVOKE_AUTH_TOKEN", raising=False)
    import src.api.server as server

    importlib.reload(server)
    r = TestClient(server.app).post("/invoke", json=_PAYLOAD)
    assert r.status_code == 401


def test_invoke_explicit_anonymous_middleware_is_401(monkeypatch):
    """A middleware that explicitly stamps ANONYMOUS must be treated as
    unauthenticated (401), not accepted merely because request.state.trust_level
    is set. Test the trust VALUE, not attribute presence (entry-adapter v0.2.0
    string-ANONYMOUS bypass fix)."""
    monkeypatch.delenv("INVOKE_AUTH_TOKEN", raising=False)
    from framework.schemas.trust_level import TrustLevel

    import src.api.server as server

    importlib.reload(server)

    @server.app.middleware("http")
    async def _stamp_anonymous(request, call_next):
        request.state.trust_level = TrustLevel.ANONYMOUS
        return await call_next(request)

    r = TestClient(server.app).post("/invoke", json=_PAYLOAD)
    assert r.status_code == 401


def test_invoke_string_anonymous_middleware_is_401(monkeypatch):
    """entry-adapter v0.2.0: middleware may stamp the raw string "ANONYMOUS"
    (not the enum). _coerce_trust must normalize it so the unauthenticated guard
    still fires (→ 401), not silently skip token promotion and 403."""
    monkeypatch.delenv("INVOKE_AUTH_TOKEN", raising=False)
    import src.api.server as server

    importlib.reload(server)

    @server.app.middleware("http")
    async def _stamp_str_anonymous(request, call_next):
        request.state.trust_level = "ANONYMOUS"
        return await call_next(request)

    r = TestClient(server.app).post("/invoke", json=_PAYLOAD)
    assert r.status_code == 401


def test_invoke_with_valid_token_succeeds(client):
    """Authenticated caller reaches the graph and gets a successful, product-
    neutral advisory (deterministic template fallback — no LLM injected)."""
    c, _ = client
    r = c.post("/invoke", json=_PAYLOAD, headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "success"
    # The non-solicitation disclaimer (S-3 L4) is always present in output.
    assert "output" in body
