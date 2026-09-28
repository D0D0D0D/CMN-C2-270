# CMN-C2-270 — Unit tests: deterministic life-plan calculation

from src.services.lifeplan_calc import (
    DEFAULT_PARAMS,
    estimate_annual_pension,
    project_life_plan,
)


class TestProjectLifePlan:
    def test_deterministic_same_input_same_output(self):
        """Determinism (auditability): identical input → identical output."""
        profile = {"current_age": 30, "current_assets": 1_000_000, "annual_income": 5_000_000, "annual_expense": 4_000_000}
        r1 = project_life_plan(profile)
        r2 = project_life_plan(profile)
        assert r1 == r2

    def test_trajectory_spans_to_projection_end_age(self):
        profile = {"current_age": 40}
        r = project_life_plan(profile, {"projection_end_age": 100})
        ages = [row["age"] for row in r["trajectory"]]
        assert ages[0] == 40
        assert ages[-1] == 100
        assert len(ages) == 61

    def test_growth_when_income_exceeds_expense(self):
        profile = {"current_age": 30, "current_assets": 0, "annual_income": 6_000_000, "annual_expense": 4_000_000}
        r = project_life_plan(profile, {"retirement_age": 65, "projection_end_age": 66})
        # Positive net cash flow with positive return → final balance grows.
        assert r["final_balance"] > 0

    def test_depletion_detected(self):
        profile = {"current_age": 60, "current_assets": 1_000_000, "annual_income": 0, "annual_expense": 3_000_000, "annual_pension": 0}
        r = project_life_plan(profile, {"retirement_age": 60, "projection_end_age": 70})
        assert r["depletion_age"] is not None

    def test_config_overrides_defaults(self):
        profile = {"current_age": 30}
        r = project_life_plan(profile, {"annual_return_rate": 0.10})
        assert r["params"]["annual_return_rate"] == 0.10
        # untouched keys fall back to defaults
        assert r["params"]["inflation_rate"] == DEFAULT_PARAMS["inflation_rate"]


class TestEstimateAnnualPension:
    def test_estimate_is_deterministic_and_positive(self):
        profile = {"avg_annual_income": 5_000_000, "pension_enrolled_years": 40}
        v1 = estimate_annual_pension(profile)
        v2 = estimate_annual_pension(profile)
        assert v1 == v2
        assert v1 > 0
