import json
from pathlib import Path

import pandas as pd

from rootzone_mpc.data.dynamic_noise import (
    assess_dynamic_noise,
    load_dynamic_noise_config,
)


ROOT = Path(__file__).resolve().parents[1]


def inputs():
    config = load_dynamic_noise_config(ROOT / "configs/dynamic_noise_decomposition_v1.yaml")
    frame = pd.read_csv(ROOT / config["dynamic_input"])
    model = json.loads((ROOT / config["model_result"]).read_text(encoding="utf-8"))
    calibration = json.loads(
        (ROOT / config["calibration_result"]).read_text(encoding="utf-8")
    )
    return frame, model, calibration, config


def test_frozen_example_refuses_process_noise_when_measurement_floor_is_higher():
    frame, model, calibration, config = inputs()
    result = assess_dynamic_noise(frame, model, calibration, config)
    assert result["decision"] == "not_estimable_measurement_floor_exceeds_dynamic_residual"
    assert result["process_noise_sd_m3_m3"] is None
    assert result["validation_cycle_count"] == 2
    assert result["validation_transition_count"] == 94
    assert all(
        layer["known_error_variance"] > layer["residual_variance"]
        for layer in result["layers"].values()
    )


def test_identification_rows_do_not_change_validation_residual_decomposition():
    frame, model, calibration, config = inputs()
    changed = frame.copy()
    mask = changed["dataset_role"] == "identification"
    changed.loc[mask, "theta_10cm_m3_m3"] = 0.60
    changed.loc[mask, "irrigation_delivered_mm"] = 20.0
    first = assess_dynamic_noise(frame, model, calibration, config)
    second = assess_dynamic_noise(changed, model, calibration, config)
    assert first["layers"] == second["layers"]


def test_positive_remainder_is_labeled_screening_candidate_not_pure_process_noise():
    frame, model, calibration, config = inputs()
    reduced_noise = json.loads(json.dumps(calibration))
    for sensor in reduced_noise["moisture_calibration"]["sensors"]:
        sensor["corrected_residual_sd_m3_m3"] = 1.0e-6
    for device in reduced_noise["flow_calibration"]["devices"]:
        device["corrected_relative_rmse"] = 1.0e-6
    result = assess_dynamic_noise(frame, model, reduced_noise, config)
    assert result["decision"] == "unexplained_dynamic_variance_screening_candidate"
    assert result["process_noise_sd_m3_m3"] is not None
    assert all(value > 0 for value in result["process_noise_sd_m3_m3"].values())
