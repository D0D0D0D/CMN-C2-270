# CMN-C2-270 — Unit tests: peer-trend retrieval with k-anonymity

from src.services import peer_trend_store
from src.services.peer_trend_qdrant_adapter import PeerTrendQdrantAdapter
from src.services.peer_trend_store import retrieve_peer_trends


class _FakeStore:
    def __init__(self, hits):
        self._hits = hits

    def search(self, query, top_k=10):
        return self._hits


def _teardown():
    peer_trend_store.set_store(None)


class TestKAnonymity:
    def test_qdrant_payload_is_mapped_to_k_gate_metadata(self):
        sdk_store = _FakeStore(
            [
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "payload": {
                        "trend": "企業型DCを月2万円活用",
                        "group_size": 25,
                        "age_band": "30代",
                        "job_grade": "主任",
                        "metric": {"median_monthly_contribution": 20000},
                    },
                    "score": 0.91,
                    "similarity": 0.91,
                }
            ]
        )
        peer_trend_store.set_store(PeerTrendQdrantAdapter(sdk_store))
        try:
            out, unavailable = retrieve_peer_trends("30代 主任", k_anonymity_min=10)
            assert out == [
                {
                    "trend": "企業型DCを月2万円活用",
                    "age_band": "30代",
                    "job_grade": "主任",
                    "metric": {"median_monthly_contribution": 20000},
                    "group_size": 25,
                }
            ]
            assert unavailable is False
        finally:
            _teardown()

    def test_qdrant_payload_missing_or_below_k_is_dropped_fail_closed(self):
        sdk_store = _FakeStore(
            [
                {"id": "missing", "payload": {"trend": "no size"}, "score": 0.9, "similarity": 0.9},
                {
                    "id": "below",
                    "payload": {"trend": "small group", "group_size": 9},
                    "score": 0.8,
                    "similarity": 0.8,
                },
            ]
        )
        peer_trend_store.set_store(PeerTrendQdrantAdapter(sdk_store))
        try:
            out, unavailable = retrieve_peer_trends("q", k_anonymity_min=10)
            assert out == []
            assert unavailable is False
        finally:
            _teardown()

    def test_below_k_dropped(self):
        peer_trend_store.set_store(_FakeStore([
            {"text": "big", "group_size": 25, "metadata": {"group_size": 25, "age_band": "30代"}},
            {"text": "small", "group_size": 3, "metadata": {"age_band": "30代"}},
        ]))
        try:
            out, unavailable = retrieve_peer_trends("30代 主任", k_anonymity_min=10)
            assert len(out) == 1
            assert out[0]["group_size"] == 25
            assert unavailable is False
        finally:
            _teardown()

    def test_missing_group_size_is_dropped_fail_closed(self):
        peer_trend_store.set_store(_FakeStore([
            {"text": "no size", "metadata": {"age_band": "40代"}},
        ]))
        try:
            out, unavailable = retrieve_peer_trends("q", k_anonymity_min=10)
            # Store answered (rows dropped by k-gate) → NOT an outage.
            assert out == []
            assert unavailable is False
        finally:
            _teardown()

    def test_no_store_reports_unavailable(self):
        peer_trend_store.set_store(None)
        out, unavailable = retrieve_peer_trends("q")
        assert out == []
        assert unavailable is True

    def test_all_below_k_is_empty_but_not_unavailable(self):
        peer_trend_store.set_store(_FakeStore([
            {"text": "a", "group_size": 5},
            {"text": "b", "group_size": 9},
        ]))
        try:
            out, unavailable = retrieve_peer_trends("q", k_anonymity_min=10)
            # Genuinely empty (nothing met k) must be distinguishable from an
            # outage: the store answered, so unavailable is False.
            assert out == []
            assert unavailable is False
        finally:
            _teardown()

    def test_sanitized_output_has_no_raw_identifiers(self):
        peer_trend_store.set_store(_FakeStore([
            {"text": "trend", "group_size": 50, "metadata": {"group_size": 50, "age_band": "30代", "job_grade": "主任", "employee_id": "SECRET-123"}},
        ]))
        try:
            out, unavailable = retrieve_peer_trends("q", k_anonymity_min=10)
            assert out and "employee_id" not in out[0]
            assert set(out[0].keys()) == {"trend", "age_band", "job_grade", "metric", "group_size"}
            assert unavailable is False
        finally:
            _teardown()

    def test_store_outage_at_query_time_reports_unavailable(self):
        """A lazy vector-store client that connects on first search may raise at
        query time even though construction succeeded. That is a non-fatal
        outcome (029-type): retrieval degrades to [] so the invoke still
        succeeds with projection-only advice — it must NOT propagate an error —
        AND it must report store_unavailable=True so ops can alert on a
        prolonged outage without mistaking it for 'no matching segment'."""

        class _RaisingStore:
            def search(self, query, top_k=10):
                raise ConnectionError("vector store unreachable")

        peer_trend_store.set_store(_RaisingStore())
        try:
            out, unavailable = retrieve_peer_trends("q", k_anonymity_min=10)
            assert out == []
            assert unavailable is True
        finally:
            _teardown()
