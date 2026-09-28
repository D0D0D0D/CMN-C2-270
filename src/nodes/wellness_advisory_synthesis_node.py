"""AgentCore Platform v1.0 — CMN-C2-270 WellnessAdvisorySynthesisNode (inner)."""

# Inner domain-workflow node (third step). Synthesizes the projection + peer
# trends into "material for the employee to think with". S-3 guard L1 (prompt
# suppression) + L2 (post-generation scan with bounded regeneration).

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.status_util import is_error as _is_error
from src.services.llm_client import get_llm
from src.services.product_guard import scan_for_product_recommendation

# L1 — system prompt suppression of individual product recommendation.
_SYSTEM_PROMPT = (
    "あなたは企業の従業員が自分の資産形成を自分で考えるための参考情報を提示するアシスタントです。"
    "厳守事項: 特定の金融商品（個別の株式・投資信託・保険商品の銘柄名、証券会社名、ティッカー等）を"
    "一切挙げず、推奨も勧誘もしないこと。一般的な資産形成の考え方と、提示された社内の匿名化傾向の"
    "解釈に限定して、本人が判断するための材料を中立的に述べること。"
)

_DEFAULT_REGEN_MAX = 2  # up to 2 regenerations = 3 total attempts


class WellnessAdvisorySynthesisNode(FunctionNode):
    """LLM synthesis with S-3 L1/L2 product-recommendation guard."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict) -> dict:
        if _is_error(state.get("status")):
            return {}

        knobs = state.get("runtime_knobs", {}) if isinstance(state.get("runtime_knobs"), dict) else {}
        regen_max = int(knobs.get("guard_regen_max", _DEFAULT_REGEN_MAX))
        extra_terms = knobs.get("product_name_blocklist") or []

        projection = state.get("plan_projection", {}) or {}
        peer_trends = state.get("peer_trends", []) or []
        user_prompt = self._build_user_prompt(projection, peer_trends)

        llm = get_llm()
        attempts = 0
        text = ""
        violations: list[str] = []
        # L2 — generate, scan, regenerate up to regen_max times.
        while attempts <= regen_max:
            text = self._generate(llm, user_prompt, attempts)
            violations = scan_for_product_recommendation(text, extra_terms)
            if not violations:
                break
            attempts += 1

        if violations:
            emit_trace_event(
                "advisory_guard_rejected",
                {"attempts": attempts, "violations": violations[:5]},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_code": "product_recommendation_guard_failed",
                "error_type": "policy_guard",
                "error_log": [
                    "product_recommendation_guard_failed: WellnessAdvisorySynthesisNode "
                    f"could not produce clean output after {attempts} regenerations"
                ],
            }

        emit_trace_event(
            "advisory_synthesized",
            {"attempts": attempts, "used_llm": llm is not None, "peer_trend_count": len(peer_trends)},
            state,
        )
        return {
            "advisory_output": text,
            "status": AgentStatus.SUCCESS.value,
        }

    def _generate(self, llm, user_prompt: str, attempt: int) -> str:
        """Call the injected LLM, or fall back to a deterministic template when
        no LLM is configured (keeps the graph runnable/testable offline)."""
        if llm is None:
            return self._template_fallback(user_prompt)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        response = llm.complete(messages)
        return (response or {}).get("content", "") or ""

    @staticmethod
    def _build_user_prompt(projection: dict, peer_trends: list) -> str:
        final_balance = projection.get("final_balance")
        depletion = projection.get("depletion_age")
        lines = [
            "以下の情報をもとに、本人が資産形成を考えるための一般的な参考情報を作成してください。",
            f"- 試算: 最終資産残高={final_balance}、資産が尽きる年齢={depletion}",
        ]
        if peer_trends:
            lines.append(f"- 社内の匿名化傾向（{len(peer_trends)}件・同年代/同職位の集計）:")
            for t in peer_trends[:5]:
                lines.append(f"  ・{t.get('age_band','')} {t.get('job_grade','')}: {t.get('trend','')}")
        else:
            lines.append("- 社内の匿名化傾向: 該当する十分な母集団がないため参考傾向なし")
        return "\n".join(lines)

    @staticmethod
    def _template_fallback(user_prompt: str) -> str:
        """Deterministic, product-neutral fallback (never names a product)."""
        return (
            "ご入力の試算と社内の匿名化傾向をふまえた一般的な資産形成の考え方です。"
            "まずは家計の収支と長期の積立可能額を確認し、目的（老後・教育・住宅等）ごとに"
            "時間分散・資産分散の観点で無理のない計画を検討することが一般的です。"
            "社内の同年代・同職位の傾向は、あくまで参考として自分の状況に当てはめてお考えください。"
        )
