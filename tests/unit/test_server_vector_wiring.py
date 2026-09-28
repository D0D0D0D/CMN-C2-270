"""Startup policy tests for the optional SDK Qdrant peer-trend store."""

from __future__ import annotations

import asyncio

import pytest
from src.services import peer_trend_store


class _Secrets:
    def __init__(self, value: str = "test-embedding-key") -> None:
        self.value = value

    def require(self, key: str) -> str:
        assert key == "PEER_TREND_EMBEDDING_API_KEY"
        return self.value


def _valid_config():
    return {
        "vector_store": {
            "enabled": True,
            "url": "http://qdrant:6333",
            "collection_name": "peer_trends",
            "namespace": "cmn",
            "agent_name": "cmn_c2_270",
            "vector_size": 1536,
            "distance": "cosine",
            "timeout_s": 30,
            "write_timeout_s": 60,
            "max_batch_documents": 1000,
            "max_top_k": 100,
            "embedding_base_url": "https://embedding.example/v1",
            "embedding_model": "text-embedding-3-small",
            "embedding_api_version": "2024-02-15-preview",
            "embedding_provider": "openai",
        }
    }


def test_store_absent_preserves_projection_only_degradation():
    import src.api.server as server

    peer_trend_store.set_store(object())
    asyncio.run(server._wire_dependencies(config={}, secret_provider=_Secrets()))
    assert peer_trend_store.get_store() is None


def test_store_present_but_invalid_fails_startup():
    import src.api.server as server

    with pytest.raises(ValueError, match="missing required keys"):
        asyncio.run(
            server._wire_dependencies(
                config={"vector_store": {"enabled": True, "url": "http://qdrant:6333"}},
                secret_provider=_Secrets(),
            )
        )
    assert peer_trend_store.get_store() is None


def test_enabled_store_uses_secret_and_runtime_lifecycle(monkeypatch):
    import src.api.server as server

    constructed: dict[str, object] = {}

    class _Runtime:
        closed = False

        async def aclose(self, *, timeout=None):
            assert timeout == 5.0
            self.closed = True

    class _Qdrant:
        def __init__(self, config, *, runtime):
            constructed["config"] = config
            constructed["runtime"] = runtime

        def search(self, query, top_k=10):
            return []

    runtime = _Runtime()
    monkeypatch.setattr(server, "AsyncServiceRuntime", lambda: runtime)
    monkeypatch.setattr(server, "QdrantVectorStore", _Qdrant)
    asyncio.run(server._wire_dependencies(config=_valid_config(), secret_provider=_Secrets()))
    assert constructed["runtime"] is runtime
    assert constructed["config"]["embedding_api_key"] == "test-embedding-key"
    assert isinstance(peer_trend_store.get_store(), server.PeerTrendQdrantAdapter)

    asyncio.run(server._shutdown_dependencies())
    assert runtime.closed is True
    assert peer_trend_store.get_store() is None


def test_qdrant_constructor_failure_is_not_silently_degraded(monkeypatch):
    import src.api.server as server

    class _Runtime:
        closed = False

        async def aclose(self, *, timeout=None):
            self.closed = True

    class _InvalidQdrant:
        def __init__(self, config, *, runtime):
            raise ValueError("invalid Qdrant configuration")

    runtime = _Runtime()
    monkeypatch.setattr(server, "AsyncServiceRuntime", lambda: runtime)
    monkeypatch.setattr(server, "QdrantVectorStore", _InvalidQdrant)

    with pytest.raises(ValueError, match="invalid Qdrant configuration"):
        asyncio.run(server._wire_dependencies(config=_valid_config(), secret_provider=_Secrets()))
    assert runtime.closed is True
    assert peer_trend_store.get_store() is None
