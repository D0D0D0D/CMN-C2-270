"""In-company peer-trend retrieval with k-anonymity enforcement.

The peer-trend RAG is the moat: each company's own anonymized asset-formation
trends (corporate DC / iDeCo / ESOP / retirement-plan usage).

Company isolation is a DEPLOYMENT boundary, not a query-time filter: this agent
is deployed one-company-per-deployment, and `vector_store.url` points
at that single company's dedicated store. There is therefore NO company/tenant scope argument
on `store.search()` — the store by construction holds one company's rows, so a
per-query company key would filter a single-company set and change nothing (it
would be a decorative "gate"). Cross-company isolation is enforced by the
per-deployment store credential, not here. (Do NOT re-introduce a scope key with
a "default" fallback — it silently matches everything; see parts/README
"tenant_id/org_id" note.)

Retrieval is a vector search over that per-deployment store, followed by
k-anonymity gating: a segment (same age-band / same job-grade) is only returned
if it aggregates at least `k_anonymity_min` individuals — otherwise it is
dropped (fail-closed) so no individual can be re-identified.

The vector store is injected (same seam pattern as the LLM) so PB-6 can build
the node with no args and tests can supply a fake store.
"""

from __future__ import annotations

from typing import Any

DEFAULT_K_ANONYMITY_MIN = 10

_STORE: Any | None = None


def set_store(store: Any | None) -> None:
    """Inject the vector store (real store in server, fake in tests)."""
    global _STORE
    _STORE = store


def get_store() -> Any | None:
    return _STORE


def retrieve_peer_trends(
    query: str,
    top_k: int = 10,
    k_anonymity_min: int = DEFAULT_K_ANONYMITY_MIN,
) -> tuple[list[dict], bool]:
    """Search the per-deployment (single-company) trend store and return only
    k-anonymous rows.

    Each store hit is expected to carry an aggregated `group_size` (how many
    individuals the anonymized trend row summarizes). Rows with
    group_size < k_anonymity_min are dropped — they could enable
    re-identification.

    Returns a ``(trends, store_unavailable)`` tuple. ``store_unavailable`` is
    True when the store is not configured OR unreachable at query time, and
    False when the store answered (even if nothing survived the k-gate). The
    caller uses this flag to emit an observability signal so a prolonged store
    outage is distinguishable from a genuinely empty peer-trend result — without
    changing the outcome (both still degrade to projection-only advice). The
    flag carries no profile or secret data.
    """
    store = get_store()
    if store is None:
        return [], True

    # A store outage at query time (lazy client that connects on first search)
    # is a non-fatal, non-error outcome: the synthesis node degrades to
    # projection-only advice. This mirrors the "store not configured" path and
    # keeps a transient vector-store failure from turning the whole invoke into
    # an error (029-type graceful degradation). It never masks a k-anonymity
    # failure — the k-gate below still runs on whatever hits are returned.
    try:
        hits = store.search(query, top_k=top_k) or []
    except Exception:
        return [], True
    survivors: list[dict] = []
    for hit in hits:
        group_size = _group_size(hit)
        if group_size is not None and group_size >= k_anonymity_min:
            survivors.append(_sanitize(hit, group_size))
    return survivors, False


def _group_size(hit: dict) -> int | None:
    """Extract the aggregated group size from a store hit (top-level or in
    metadata). Returns None when absent — absence is treated as NOT k-anonymous
    (fail-closed), so such a row is dropped."""
    if "group_size" in hit:
        try:
            return int(hit["group_size"])
        except (TypeError, ValueError):
            return None
    meta = hit.get("metadata") or {}
    if "group_size" in meta:
        try:
            return int(meta["group_size"])
        except (TypeError, ValueError):
            return None
    return None


def _sanitize(hit: dict, group_size: int) -> dict:
    """Return only the anonymized, JSON-safe fields for downstream use.

    We never propagate raw identifiers — only the aggregated trend text/metrics
    and the surviving group_size (evidence that the k-gate passed).
    """
    meta = hit.get("metadata") or {}
    return {
        "trend": hit.get("text") or meta.get("trend") or "",
        "age_band": meta.get("age_band", ""),
        "job_grade": meta.get("job_grade", ""),
        "metric": meta.get("metric", {}),
        "group_size": group_size,
    }
