import numpy as np
import pandas as pd

from rootzone_mpc.experiments.confirmation import (
    _bootstrap_median_interval,
    _classify,
    _subgroup_results,
)


def test_bootstrap_interval_is_reproducible():
    values = np.array([-2.0, -1.0, 0.0, 1.0, 2.0])
    first = _bootstrap_median_interval(values, 2000, 17)
    second = _bootstrap_median_interval(values, 2000, 17)
    assert first == second


def test_tradeoff_classification_requires_no_extra_safety():
    pairs = pd.DataFrame(
        {
            "delta_deficit_integral": [0.1, 0.2, 0.3],
            "delta_irrigation_command_total_mm": [-2.0, -3.0, -4.0],
            "rule_safety_violation_steps": [0, 0, 0],
            "mpc_safety_violation_steps": [0, 0, 0],
        }
    )
    config = {
        "confirmation": {
            "tie_tolerance": {
                "deficit_integral": 1e-6,
                "irrigation_command_total_mm": 0.1,
            },
            "joint_nonworse_ratio_min": 0.6,
            "any_endpoint_adverse_ratio_max": 0.25,
        }
    }
    intervals = {
        "deficit_integral": {"ci95_upper": 0.3},
        "irrigation_command_total_mm": {"ci95_upper": -1.0},
    }
    label, diagnostics = _classify(pairs, config, intervals)
    assert label == "WATER_SAVING_WITH_DEFICIT_TRADEOFF"
    assert diagnostics["no_extra_safety_violation"]


def test_subgroup_results_cover_prespecified_factors():
    pairs = pd.DataFrame(
        {
            "scenario_id": [f"E{i:04d}" for i in range(1, 7)],
            "et_profile": ["typical", "hot_spell", "variable_cloud"] * 2,
            "forecast_bias_fraction": [-0.2, -0.1, 0.0, 0.05, 0.1, 0.2],
            "root_depth_mm": [240, 260, 280, 300, 320, 340],
            "irrigation_efficiency": [0.72, 0.76, 0.80, 0.84, 0.88, 0.92],
            "drainage_coefficient": [10, 12, 14, 18, 22, 26],
            "delta_deficit_integral": [-0.1, 0.0, 0.1, -0.2, 0.2, 0.3],
            "delta_irrigation_command_total_mm": [-2, -2, -2, -4, -4, -4],
        }
    )
    subgroup = _subgroup_results(pairs)
    assert set(subgroup["factor"]) == {
        "et_profile",
        "forecast_bias_group",
        "root_depth_group",
        "irrigation_efficiency_group",
        "drainage_group",
    }
