"""LLM injection seam.

Nodes must be constructible with no arguments (PB-6 instantiates every node as
`NodeClass()`), so the LLM is not passed to the constructor. Instead nodes call
`get_llm()` at execute() time. Tests inject a fake via `set_llm()`; the API
server injects the real client (shared.services.llm.anthropic_client) at startup.

`get_llm()` returns None when nothing is injected — the synthesis node degrades
to a deterministic template so the graph is still testable without a live LLM.
"""

from __future__ import annotations

from typing import Any

_LLM: Any | None = None


def set_llm(llm: Any | None) -> None:
    """Inject the LLM client (real client in server, fake in tests)."""
    global _LLM
    _LLM = llm


def get_llm() -> Any | None:
    """Return the injected LLM client, or None when not configured."""
    return _LLM
