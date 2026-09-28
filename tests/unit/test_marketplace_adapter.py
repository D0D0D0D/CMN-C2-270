"""Marketplace entry adapter and Azure LLM boundary tests."""

from __future__ import annotations

import json

import pytest
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.base import NullProvider
from framework.secrets.context import bound_secrets

import cli
from src.services import llm_client
from src.services.azure_llm import AzureLLM

_PROFILE = json.dumps(
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


@pytest.fixture(autouse=True)
def _reset_llm():
    llm_client.set_llm(None)
    try:
        yield
    finally:
        llm_client.set_llm(None)


def _set_required_environment(monkeypatch, suffix: str = "one") -> dict[str, str]:
    values = {key: f"test-{suffix}-{key.lower()}" for key in cli._REQUIRED_SECRET_KEYS}
    # The startup gate requires a bare Azure resource endpoint, so
    # this value cannot be a placeholder like the other secrets.
    values["AZURE_OPENAI_ENDPOINT"] = f"https://{suffix}.services.ai.azure.example"
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def _contains(value, needle: str) -> bool:
    if isinstance(value, dict):
        return any(_contains(key, needle) or _contains(item, needle) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains(item, needle) for item in value)
    return needle in value if isinstance(value, str) else False


def test_marketplace_conversation_history_never_enters_state_or_output():
    sentinel = "UNMASKED-CONVERSATION-PII-270"
    agent = cli.MarketplaceGraph(config={"k_anonymity_min": 10})
    agent.compile()
    original_invoke = agent._compiled.invoke
    captured = {}

    def capture_state(initial_state, config=None):
        captured["initial"] = initial_state
        final_state = original_invoke(initial_state, config=config)
        captured["final"] = final_state
        return final_state

    agent._compiled.invoke = capture_state
    result = agent.invoke(
        _PROFILE,
        ctx=InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL),
        input_context={"conversation_history": [{"role": "user", "content": sentinel}]},
    )

    assert captured["initial"]["input_context"] == {}
    assert not _contains(captured["initial"], sentinel)
    assert not _contains(captured["final"], sentinel)
    assert not _contains(result, sentinel)


def test_secret_provider_resolves_only_allowlisted_process_environment(monkeypatch):
    values = _set_required_environment(monkeypatch)
    monkeypatch.setenv("UNRELATED_PLATFORM_SECRET", "must-not-be-visible")
    agent = cli.MarketplaceGraph()

    agent.provision_secrets(NullProvider())

    provider = agent._secrets_provider
    assert {key: provider.require(key) for key in cli._REQUIRED_SECRET_KEYS} == values
    assert provider.get("UNRELATED_PLATFORM_SECRET") is None
    assert "must-not-be-visible" not in repr(vars(provider))


@pytest.mark.parametrize("missing_key", cli._REQUIRED_SECRET_KEYS)
def test_missing_required_secret_fails_during_startup_before_invoke(monkeypatch, missing_key):
    _set_required_environment(monkeypatch)
    monkeypatch.delenv(missing_key)
    agent = cli.MarketplaceGraph()
    invoked = False

    def invoke(*args, **kwargs):
        nonlocal invoked
        invoked = True

    agent.invoke = invoke
    with pytest.raises(Exception) as exc_info:
        agent.provision_secrets(NullProvider())

    assert missing_key in str(exc_info.value)
    assert not invoked


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.cognitiveservices.azure.com/openai/responses?api-version=2025-04-01-preview",
        "https://example.services.ai.azure.example/openai",
        "https://example.services.ai.azure.example/v1",
        "https://example.services.ai.azure.example?api-version=2024-10-21",
        "example.services.ai.azure.example",
    ],
    ids=["api-path", "openai-path", "base-path", "query-only", "missing-scheme"],
)
def test_unusable_azure_endpoint_shape_fails_during_startup(monkeypatch, endpoint):
    """AzureOpenAIClient appends its own path, so anything but a bare endpoint breaks.

    The resulting error surfaces inside complete(); a node that swallows it
    degrades silently while the invocation still reports status: success, so the
    adapter has to reject the shape at startup instead.
    """

    _set_required_environment(monkeypatch)
    agent = cli.MarketplaceGraph()
    invoked = False
    def invoke(*args, **kwargs):
        nonlocal invoked
        invoked = True
    agent.invoke = invoke
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", endpoint)

    with pytest.raises(ValueError, match="bare"):
        agent.provision_secrets(NullProvider())
    assert not invoked


def test_azure_llm_resolves_fresh_values_without_retaining_them(monkeypatch):
    import shared.services.llm.azure_openai_client as azure_module

    configs = []

    class FakeAzureOpenAIClient:
        def __init__(self, config):
            configs.append(dict(config))

        def complete(self, messages):
            return {"content": "ok", "messages": messages}

        def stream(self, messages):
            yield "ok"

        def bind_tools(self, tools):
            return self

    monkeypatch.setattr(azure_module, "AzureOpenAIClient", FakeAzureOpenAIClient)
    provider = cli._AllowlistedEnvironmentProvider()
    llm = AzureLLM()
    assert vars(llm) == {}

    with bound_secrets(provider):
        first = _set_required_environment(monkeypatch, "first")
        llm.complete([{"role": "user", "content": "first"}])
        assert vars(llm) == {}

        second = _set_required_environment(monkeypatch, "second")
        llm.complete([{"role": "user", "content": "second"}])
        assert vars(llm) == {}

    assert configs == [
        {
            "api_key": first["AZURE_OPENAI_API_KEY"],
            "azure_endpoint": first["AZURE_OPENAI_ENDPOINT"],
            "azure_deployment": first["AZURE_OPENAI_DEPLOYMENT"],
        },
        {
            "api_key": second["AZURE_OPENAI_API_KEY"],
            "azure_endpoint": second["AZURE_OPENAI_ENDPOINT"],
            "azure_deployment": second["AZURE_OPENAI_DEPLOYMENT"],
        },
    ]


def test_azure_llm_bind_tools_returns_a_new_instance():
    llm = AzureLLM()
    bound = llm.bind_tools([{"name": "lookup"}])

    assert bound is not llm
    assert vars(llm) == {}
    assert vars(bound) == {"_tools": ({"name": "lookup"},)}


def test_main_loads_repo_config_for_marketplace_runner(monkeypatch):
    called = {}

    def fake_runner(agent_cls, **kwargs):
        called["agent_cls"] = agent_cls
        called.update(kwargs)

    monkeypatch.setattr(cli, "run_agent_marketplace", fake_runner)
    cli.main()

    assert called["agent_cls"] is cli.MarketplaceGraph
    assert called["agent_name"] == "CMN-C2-270"
    assert called["namespace"] == "cmn"
    assert called["config"]["max_retry"] == 3
    assert called["config"]["calc_config"]["retirement_age"] == 65
