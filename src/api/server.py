"""AgentCore Platform v1.0 — CMN-C2-270 HTTP entry point.

Entry points are adapters only — no business logic here. For platform-level
routing, AgentGateway calls agent.invoke() directly.

Entry authentication (src/entry_adapter/auth.py):
  - a verified middleware trust (request.state.trust_level) is used as-is;
  - otherwise INVOKE_AUTH_TOKEN Bearer auth promotes ANONYMOUS →
    VERIFIED_EXTERNAL (_resolve_trust raises 401 on token mismatch);
  - an unauthenticated caller (no verified middleware trust AND no token
    promotion) is rejected with 401. The old fallback that defaulted such
    callers to VERIFIED_EXTERNAL was fail-open (S-1 bypass) and is removed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import yaml  # type: ignore[import-untyped]
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from shared.secrets import factory as secrets_factory
from shared.services.runtime import AsyncServiceRuntime
from shared.services.vector_store.qdrant_vector_store import QdrantVectorStore

from src.entry_adapter import Operation, authenticate
from src.graph.graph import WellnessAdvisoryGraph
from src.services.peer_trend_qdrant_adapter import PeerTrendQdrantAdapter
from src.services.peer_trend_store import set_store

_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
_PEER_TREND_STORE_CONFIG_KEY = "vector_store"
_PEER_TREND_EMBEDDING_API_KEY_SECRET = "PEER_TREND_EMBEDDING_API_KEY"
_QDRANT_CONFIG_KEYS = frozenset(
    {
        "url",
        "collection_name",
        "namespace",
        "agent_name",
        "vector_size",
        "distance",
        "timeout_s",
        "write_timeout_s",
        "max_batch_documents",
        "max_top_k",
        "embedding_base_url",
        "embedding_model",
        "embedding_api_version",
        "embedding_provider",
    }
)
_service_runtime: AsyncServiceRuntime | None = None


def _load_runtime_config(path: Path = _RUNTIME_CONFIG_PATH) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise RuntimeError("config/config.yaml must contain a mapping")
    return loaded


agent = WellnessAdvisoryGraph(config=_load_runtime_config())
agent.compile()
_secret_provider = secrets_factory(namespace="cmn", agent_name="cmn_c2_270")
agent.provision_secrets(_secret_provider)


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    runtime_config = getattr(application.state, "service_config", agent.config)
    secret_provider = getattr(application.state, "secret_provider", _secret_provider)
    await _wire_dependencies(config=runtime_config, secret_provider=secret_provider)
    try:
        yield
    finally:
        await _shutdown_dependencies()


app = FastAPI(title="Employee Financial Wellness Self-Advisory Agent", lifespan=_lifespan)


async def _wire_dependencies(*, config: Mapping[str, Any], secret_provider: Any) -> None:
    """Inject the real LLM + peer-trend vector store into the node seams.

    Construction occurs in FastAPI startup so the Qdrant runtime has an explicit
    process lifecycle. An absent/disabled store retains the 029-type degradation;
    a present but invalid store configuration is deliberately allowed to abort
    startup instead of being hidden as "not configured".
    """
    global _service_runtime

    # LLM seam — synthesis node falls back to the deterministic, product-neutral
    # template when no LLM is injected.
    try:
        from shared.services.llm.anthropic_client import AnthropicClient  # type: ignore

        from src.services.llm_client import set_llm

        set_llm(AnthropicClient())
    except Exception:
        pass

    await _shutdown_dependencies()
    raw_config = config.get(_PEER_TREND_STORE_CONFIG_KEY)
    if raw_config is None:
        return
    if not isinstance(raw_config, Mapping):
        raise ValueError(f"config.{_PEER_TREND_STORE_CONFIG_KEY} must be a mapping")

    enabled = raw_config.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(f"config.{_PEER_TREND_STORE_CONFIG_KEY}.enabled must be a boolean")
    if not enabled:
        return

    store_config = _validate_qdrant_config(raw_config)
    with bound_secrets(secret_provider):
        store_config["embedding_api_key"] = secret_provider.require(_PEER_TREND_EMBEDDING_API_KEY_SECRET)

    runtime = AsyncServiceRuntime()
    try:
        qdrant = QdrantVectorStore(store_config, runtime=runtime)
    except BaseException:
        await runtime.aclose(timeout=5.0)
        raise
    set_store(PeerTrendQdrantAdapter(qdrant))
    _service_runtime = runtime


def _validate_qdrant_config(raw_config: Mapping[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(raw_config) - _QDRANT_CONFIG_KEYS - {"enabled"})
    if unknown:
        raise ValueError(f"config.{_PEER_TREND_STORE_CONFIG_KEY} has unknown keys: {unknown}")
    missing = sorted(key for key in _QDRANT_CONFIG_KEYS if raw_config.get(key) in (None, ""))
    if missing:
        raise ValueError(f"config.{_PEER_TREND_STORE_CONFIG_KEY} is missing required keys: {missing}")
    return {key: raw_config[key] for key in _QDRANT_CONFIG_KEYS}


async def _shutdown_dependencies() -> None:
    global _service_runtime
    runtime, _service_runtime = _service_runtime, None
    set_store(None)
    if runtime is not None:
        await runtime.aclose(timeout=5.0)


def _ctx(request: Request, session_id: str) -> InvocationContext:
    """Build the InvocationContext AFTER entry authentication.

    Scope note: this template has no customer-internal KB partition
    (`SCOPE_KEYS = ()`). The peer-trend RAG is keyed only by anonymized segment
    (age-band / job-grade); company isolation is a DEPLOYMENT boundary:
    `vector_store.url` points at one company's dedicated store, so
    tenant isolation comes from the deployment boundary, not from a per-request
    scope key. There is no code path that reads request tenant/org scope. A
    default scope would be STG theater and would silently match everything.
    See src/services/peer_trend_store.py and parts/README "scope keys".
    """
    try:
        auth = authenticate(request, operation=Operation.READ_INVOKE)
    except HTTPException as exc:
        # Preserve this endpoint's HTTP contract: callers with no credentials
        # (or an explicit ANONYMOUS trust stamp and no principal) are
        # unauthenticated, not authenticated-but-forbidden.
        if exc.status_code == 403 and _anonymous_without_principal(request):
            raise HTTPException(status_code=401, detail="Authentication is required.") from exc
        raise
    return InvocationContext(
        session_id=session_id or str(uuid4()),
        caller_trust_level=auth.trust,
        caller_id=auth.principal["caller_id"],
    )


def _anonymous_without_principal(request: Request) -> bool:
    if hasattr(request.state, "caller_id"):
        return False
    trust = getattr(request.state, "trust_level", None)
    if trust is None:
        return True
    return trust in {TrustLevel.ANONYMOUS, TrustLevel.ANONYMOUS.value}


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> dict[str, object]:
    ctx = _ctx(request, req.session_id)
    with bound_secrets(agent._secrets_provider):
        return cast(dict[str, object], agent.invoke(req.input, ctx=ctx))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent": "cmn_c2_270"}
