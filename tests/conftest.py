"""Fixtures for `work/scripts/mrb_live_llm_check.py`.

The production graph still uses module-level seams for the LLM and peer-trend
store, while the live harness injects the CLI-backed client into `config["llm"]`.
The proxy below bridges that harness-local client without changing `src/`.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest
from shared.services.llm.base_llm import BaseLLM

from src.services import llm_client, peer_trend_store


class _HarnessLLMProxy(BaseLLM):
    def complete(self, messages: list) -> dict:
        return _harness_llm(self).complete(messages)

    def stream(self, messages: list):
        yield self.complete(messages)["content"]

    def bind_tools(self, tools: list) -> "_HarnessLLMProxy":
        return self


def _harness_llm(proxy: object) -> Any:
    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame is not None else None
        while frame is not None:
            candidate = frame.f_locals.get("llm")
            if candidate is not None and candidate is not proxy and hasattr(candidate, "complete") and hasattr(candidate, "calls"):
                return candidate
            frame = frame.f_back
    finally:
        del frame
    raise AssertionError("mrb_live_llm_check.py の CodexCliClient を call stack から解決できません")


class _LivePeerTrendStore:
    def search(self, query: str, top_k: int = 10) -> list[dict]:
        return [
            {
                "text": "企業型DCを月2万円活用",
                "group_size": 25,
                "metadata": {"group_size": 25, "age_band": "30代", "job_grade": "主任"},
            }
        ][:top_k]


@pytest.fixture
def agent_config() -> dict:
    llm_client.set_llm(_HarnessLLMProxy())
    peer_trend_store.set_store(_LivePeerTrendStore())
    try:
        yield {
            "k_anonymity_min": 10,
            "guard_regen_max": 2,
            "peer_top_k": 10,
            "calc_config": {
                "annual_return_rate": 0.03,
                "inflation_rate": 0.02,
                "salary_growth_rate": 0.015,
                "retirement_age": 65,
                "projection_end_age": 100,
                "pension_estimation_mode": "certificate_input",
            },
        }
    finally:
        llm_client.set_llm(None)
        peer_trend_store.set_store(None)


@pytest.fixture
def case_payload() -> str:
    return json.dumps(
        {
            "current_age": 35,
            "current_assets": 5_000_000,
            "annual_income": 6_000_000,
            "annual_expense": 4_000_000,
            "annual_pension": 1_800_000,
            "job_grade": "主任",
            "goal": "老後資金",
        },
        ensure_ascii=False,
    )
