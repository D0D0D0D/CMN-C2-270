"""Translate SDK Qdrant hits into the peer-trend service's stable hit shape."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class PeerTrendQdrantAdapter:
    """Expose Qdrant payload fields as metadata for the k-anonymity gate."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def search(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        adapted: list[dict[str, Any]] = []
        for hit in self._store.search(query, top_k=top_k) or []:
            payload = hit.get("payload")
            metadata = dict(payload) if isinstance(payload, Mapping) else {}
            adapted.append(
                {
                    "id": hit.get("id"),
                    "score": hit.get("score"),
                    "similarity": hit.get("similarity"),
                    "text": metadata.get("text") or metadata.get("trend") or metadata.get("content") or "",
                    "metadata": metadata,
                }
            )
        return adapted
