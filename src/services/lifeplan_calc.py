"""Deterministic life-plan projection — pure functions, no side effects.

Public simulator delegation was ruled out (see docs/02_design.md 変更履歴):
the FSA life-plan simulator exposes no embeddable API/program, and the MHLW
pension program covers pension only and requires an administrative application.
This module therefore implements the life-plan projection with a well-known
deterministic algorithm (future-value + year-by-year household cash-flow).

DETERMINISTIC: same input → same output. No randomness, no wall-clock reads.
Reproducibility is required for auditability (proof-of-boundary tests
and independent review).
"""

from __future__ import annotations

# Conservative defaults; every value is overridable via config/agent.yaml.
DEFAULT_PARAMS: dict = {
    "annual_return_rate": 0.03,  # long-term diversified, conservative
    "inflation_rate": 0.02,  # BoJ price target level
    "salary_growth_rate": 0.015,  # nominal, conservative
    "retirement_age": 65,
    "projection_end_age": 100,
}


def _resolve_params(config: dict | None) -> dict:
    params = dict(DEFAULT_PARAMS)
    if config:
        for key in DEFAULT_PARAMS:
            if key in config and config[key] is not None:
                params[key] = config[key]
    return params


def project_life_plan(profile: dict, config: dict | None = None) -> dict:
    """Project household asset balance from current age to projection_end_age.

    profile keys (all optional, defaulted):
        current_age:      int
        current_assets:   float (JPY)
        annual_income:    float (JPY, take-home, pre-retirement)
        annual_expense:   float (JPY)
        annual_pension:   float (JPY, from certificate input or estimate)

    Returns a JSON-safe dict:
        {
          "params": {...effective params...},
          "trajectory": [{"age": int, "balance": float}, ...],
          "final_balance": float,
          "depletion_age": int | None,   # first age balance < 0, else None
        }
    """
    p = _resolve_params(config)
    current_age = int(profile.get("current_age", 30))
    balance = float(profile.get("current_assets", 0.0))
    income = float(profile.get("annual_income", 0.0))
    expense = float(profile.get("annual_expense", 0.0))
    pension = float(profile.get("annual_pension", 0.0))

    retirement_age = int(p["retirement_age"])
    end_age = int(p["projection_end_age"])
    r = float(p["annual_return_rate"])
    salary_growth = float(p["salary_growth_rate"])

    trajectory: list[dict] = []
    depletion_age: int | None = None

    for age in range(current_age, end_age + 1):
        if age < retirement_age:
            year_income = income
            income *= 1.0 + salary_growth  # nominal raise for next year
        else:
            year_income = pension  # post-retirement income = public pension

        # Year-by-year: grow prior balance by return, add net cash flow.
        balance = balance * (1.0 + r) + year_income - expense
        balance = round(balance, 2)
        trajectory.append({"age": age, "balance": balance})
        if depletion_age is None and balance < 0:
            depletion_age = age

    return {
        "params": p,
        "trajectory": trajectory,
        "final_balance": trajectory[-1]["balance"] if trajectory else 0.0,
        "depletion_age": depletion_age,
    }


def estimate_annual_pension(profile: dict) -> float:
    """Rough public-pension estimate (used only when pension_estimation_mode
    == 'formula_estimate'; default mode receives the value from the employee's
    'nenkin teiki-bin' certificate input).

    Simplified公知 approximation: flat basic pension + earnings-related portion.
    Not an official figure — clearly an estimate. Deterministic.
    """
    # Basic pension (老齢基礎年金) full-payment annual amount, approximate.
    basic_pension = 780_000.0
    avg_annual_income = float(profile.get("avg_annual_income", profile.get("annual_income", 0.0)))
    years_enrolled = float(profile.get("pension_enrolled_years", 40))
    # Earnings-related (老齢厚生年金) rough factor 5.481/1000 per year of enrollment.
    earnings_related = avg_annual_income * 0.005481 * years_enrolled
    return round(basic_pension + earnings_related, 2)
