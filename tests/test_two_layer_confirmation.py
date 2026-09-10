import numpy as np
import pandas as pd

from rootzone_mpc.experiments.two_layer_confirmation import (
    _metric_summary,
    classify_two_layer_confirmation,
)


def confirmation():
    return {
        "tie_tolerance": {
            "weighted_deficit_integral": 1e-12,
            "irrigation_command_total_mm": 1e-12,
        },
        "joint_nonworse_ratio_min": 0.60,
        "any_primary_adverse_ratio_max": 0.25,
        "classification_labels": [
            "JOINT_VALUE_SUPPORTED",
            "WATER_SAVING_WITH_DEFICIT_TRADEOFF",
            "DEFICIT_REDUCTION_WITH_WATER_COST",
            "OVERALL_VALUE_NOT_SUPPORTED",
        ],
    }


def pairs(deficit, water, rule_safety=None, mpc_safety=None):
    count = len(deficit)
    zeros = np.zeros(count, dtype=int)
    return pd.DataFrame({
        "delta_weighted_deficit_integral": deficit,
        "delta_irrigation_command_total_mm": water,
        "rule_safety_violation_steps": zeros if rule_safety is None else rule_safety,
        "mpc_safety_violation_steps": zeros if mpc_safety is None else mpc_safety,
        "rule_shallow_safety_violation_steps": zeros,
        "mpc_shallow_safety_violation_steps": zeros,
        "rule_deep_safety_violation_steps": zeros,
        "mpc_deep_safety_violation_steps": zeros,
    })


def aggregate(deficit, water):
    return {
        "weighted_deficit_integral": _metric_summary(
            np.asarray(deficit), 1e-12, 2000, 7
        ),
        "irrigation_command_total_mm": _metric_summary(
            np.asarray(water), 1e-12, 2000, 8
        ),
    }


def test_joint_value_requires_ratio_and_no_extra_safety():
    deficit = [-0.2] * 8 + [0.0] * 2
    water = [-2.0] * 8 + [0.0] * 2
    frame = pairs(deficit, water)
    label, diagnostics = classify_two_layer_confirmation(
        frame, confirmation(), aggregate(deficit, water)
    )
    assert label == "JOINT_VALUE_SUPPORTED"
    assert diagnostics["joint_nonworse_ratio"] == 1.0


def test_water_saving_tradeoff_classification_is_preserved():
    deficit = [0.1] * 10
    water = [-2.0] * 10
    label, _ = classify_two_layer_confirmation(
        pairs(deficit, water), confirmation(), aggregate(deficit, water)
    )
    assert label == "WATER_SAVING_WITH_DEFICIT_TRADEOFF"


def test_extra_safety_forces_overall_not_supported():
    deficit = [-0.2] * 10
    water = [-2.0] * 10
    label, diagnostics = classify_two_layer_confirmation(
        pairs(deficit, water, mpc_safety=[1] + [0] * 9),
        confirmation(), aggregate(deficit, water),
    )
    assert label == "OVERALL_VALUE_NOT_SUPPORTED"
    assert diagnostics["no_extra_safety_violation"] is False
