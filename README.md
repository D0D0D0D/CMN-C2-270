# CMN-C2-270 — Employee Financial Wellness Self-Advisory Agent

> **Category**: Cat 2 (domain workflow)
> **Industry**: CMN

## Overview

Employee Financial Wellness Self-Advisory Agent lets an employee check their own long-term
financial picture themselves. Given a profile — age, assets, income, expenses, expected pension,
job grade, goal — it returns material the person can think with: a deterministic life-plan
projection to age 100, anonymised peer trends drawn from the same organisation, and a written
summary that ties the two together.

It suits a benefits or HR team offering financial wellness as self-service, and an employee who
wants to see how peers in the same age band and job grade are building assets before revisiting
their own plan. Public statistics alone cannot answer that; the organisation's own context —
how its defined-contribution plan, share-purchase plan and retirement allowance are actually
used — is what the peer-trend layer adds.

**The agent never recommends or solicits a specific financial product.** That is not a prompt
instruction alone; it is enforced structurally, in four layers:

- **Prompt suppression** — the synthesis prompt forbids naming an instrument, fund or broker.
- **Post-generation scan** — the generated text is scanned deterministically against a
  product-name, broker-name and solicitation-verb dictionary, and regenerated on a hit (up to
  `guard_regen_max` times).
- **A final output gate** — the same scan runs again in `post_process` and is *fail-closed*: a
  surviving hit ends the invocation as `status=error` / `product_recommendation_guard_failed`
  rather than returning the text.
- **Disclaimer preservation** — the non-solicitation notice is appended to every successful
  response, so it cannot be lost by regeneration.

The peer-trend layer is bounded the same way. A retrieved segment is returned only when its
group size meets `k_anonymity_min`; a segment whose size is missing or unreadable is treated as
*not* anonymous and dropped, never shown. Queries are built from the anonymised segment keys
(age band, job grade, goal) — never from a raw identifier.

The projection is arithmetic, not a model: assets are rolled forward year by year
(`previous × (1 + return) + income − expense`, plus retirement allowance and life events) under
configured assumptions. It is an estimate under stated assumptions, not a forecast, and it does
not guarantee any future result.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Integration

Provide:

- an AgentCore runtime;
- caller authentication in front of the entry adapter;
- optionally, a `BaseLLM` client and a vector store holding the anonymised peer-trend corpus.

Both optional dependencies degrade explicitly rather than silently:

| Absent | Behaviour |
|---|---|
| LLM client | The synthesis step falls back to a deterministic, product-neutral template. The response is still produced and still carries the disclaimer. |
| Peer-trend vector store (`vector_store.enabled: false`, the default) | `peer_trends` comes back empty and the response is projection-only. |
| Peer-trend store present but misconfigured | Start-up **aborts**. A half-wired store is not hidden as "not configured". |

Runtime parameters read from `config/config.yaml` (defaults in parentheses):

| Key | Meaning |
|---|---|
| `k_anonymity_min` (`10`) | Minimum group size before a peer trend is shown. Below it, the segment is withheld |
| `peer_top_k` (`10`) | Number of peer-trend candidates retrieved |
| `guard_regen_max` (`2`) | Regeneration attempts when the product-recommendation scan fires |
| `product_name_blocklist` (`[]`) | Extra product/broker terms, unioned with the built-in dictionary |
| `vector_store.*` (`enabled: false`) | Peer-trend vector store. When enabled, every field in the block is required |
| `calc_config.annual_return_rate` (`0.03`) | Assumed nominal return |
| `calc_config.inflation_rate` (`0.02`) | Assumed inflation |
| `calc_config.salary_growth_rate` (`0.015`) | Assumed salary growth |
| `calc_config.retirement_age` (`65`) / `projection_end_age` (`100`) | Projection horizon |
| `calc_config.pension_estimation_mode` (`certificate_input`) | `certificate_input` uses the figure the employee supplies; `formula_estimate` derives an approximation |

The template does not open accounts, place orders, price instruments, hold market data, or act
on behalf of the employee. Input is a profile; output is text plus structured figures.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Supplied by the platform's own package registry at build time. It is not published on public PyPI, so `pip install -e .` alone does not fetch it. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run on AGENTIC STAR. Without the `agenticstar-agentcore`
distribution installed, `src/api/server.py` cannot be imported at all — the graph, the state
schema and the security gates all come from it, and there is no vendored substitute in this
repository.

With the framework installed but no platform around it, the standalone server does start: the
graph is built and compiled at import time, and `/health` answers `{"status": "ok", "agent":
"cmn_c2_270"}`. What it will not do is answer an unauthenticated request — `/invoke` returns
**401** unless the caller is either stamped by upstream middleware or presents a matching
`INVOKE_AUTH_TOKEN` bearer token. With no LLM injected and the peer-trend store left disabled,
an authenticated request still succeeds and returns the deterministic, projection-only form.
The test suite runs without a platform connection.

## Input

`POST /invoke` takes a JSON body with `input` and `session_id`. `input` is the employee profile
as a JSON object encoded in a string; free text is also accepted and passed through as
`free_text`, but the projection needs the numeric fields to do anything useful. Recognised
profile keys are `current_age`, `current_assets`, `annual_income`, `annual_expense`,
`annual_pension`, `job_grade` and `goal`; `formula_estimate` pension mode additionally
reads `avg_annual_income` and `pension_enrolled_years`.

Callers must be authenticated. The entry adapter uses a verified middleware trust level when one
is stamped on the request; otherwise a matching `Authorization: Bearer ${INVOKE_AUTH_TOKEN}`
promotes the caller to `VERIFIED_EXTERNAL`. A caller that is neither middleware-verified nor
token-authenticated is rejected with **401** — there is no anonymous path into the graph, and
the first node requires `VERIFIED_EXTERNAL` regardless.

```bash
curl -X POST http://127.0.0.1:8000/invoke \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${INVOKE_AUTH_TOKEN}" \
  -d '{
    "input": "{\"current_age\":35,\"current_assets\":5000000,\"annual_income\":6000000,\"annual_expense\":4000000,\"annual_pension\":1800000,\"job_grade\":\"主任\",\"goal\":\"老後資金\"}",
    "session_id": "demo"
  }'
```

Input over 20,000 characters is rejected by the input gate; an empty `input` ends the invocation
as `status=error`.

## Output

The response carries `output` — the advisory text, always ending with the non-solicitation
disclaimer — alongside the intermediate values and the standard envelope fields:
`advisory_output`, `plan_projection`, `peer_trends`, `status`, `trace_id`, `correlation_id`,
`node_history`, `error_code`, `error_type` and `error_log`.

`plan_projection` holds the year-by-year figures produced by the deterministic calculation.
`peer_trends` holds only segments that met `k_anonymity_min`; it is an empty list when no store
is configured or when no segment qualified — an empty list is a normal result, not an error.

Failure is never dressed up as success. A product-recommendation hit that survives regeneration
returns `status=error` with `error_code: product_recommendation_guard_failed`; any other inner
workflow failure returns `subgraph_failed`. The inner error log is threaded out deliberately so
a compliance failure stays visible for audit rather than being swallowed.

## Security and Limitations

The first node requires verified-external trust, so an anonymous caller is stopped before any
node body runs. The profile legitimately contains personal figures — age, income, assets — and
those are handled by anonymisation downstream rather than blocked at the door; only the size
ceiling is enforced there.

Peer-trend isolation between organisations is a **deployment boundary, not a request
parameter**. This template declares no customer-partition scope key: the peer-trend store URL
points at one organisation's dedicated collection, and there is no code path that reads a tenant
or organisation scope from the request. A deployment that shares one store between organisations
would break that isolation; give each deployment its own.

Known limits of the guard: it is a deterministic dictionary and phrase scan, so it catches the
product names, broker names and solicitation phrasings it knows about. It is applied in depth
and fail-closed precisely because no single scan is complete. Extending
`product_name_blocklist` for a given deployment is expected, not exceptional.

The projection assumes a constant return, inflation and salary-growth rate; real outcomes will
differ. Pension figures are either the employee's own statement value or a formula
approximation. Output is machine-generated, assistive, and intended for the employee's own
review — it is not advice, not a solicitation, and not a substitute for a qualified adviser.

## Release

- Version: 1.0.0
- Category: fixed multi-step domain workflow (Cat 2), outer backbone wrapping an inner workflow graph
- Generation mode: `llm`, with a deterministic fallback when no client is injected
- HITL: not enabled
- Persistence: none — no checkpointer is configured
- External API clients: deployment-injected; none bundled

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection, using a fake LLM and no vector store. Running the agent
itself starts the standalone server:

```bash
uvicorn src.api.server:app --reload
```

Set `INVOKE_AUTH_TOKEN` before calling `/invoke`, or the request is rejected with 401.

## Documentation

`docs/02_design.md` carries the node flow, the state schema, the four-layer product-recommendation
guard and the security design. `docs/03_test_spec.md` records the test specification and the
verification results.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and test specifications
```

See `docs/02_design.md` for the design and `docs/03_test_spec.md` for the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.

---
