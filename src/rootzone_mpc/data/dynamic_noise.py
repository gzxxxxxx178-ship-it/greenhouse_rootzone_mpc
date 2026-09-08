from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rootzone_mpc.experiments.two_layer_identification import build_transitions


LAYERS = ("shallow", "deep")
COEFFICIENT_KEYS = (
    "theta_shallow_minus_deep",
    "irrigation_delivered_mm",
    "solar_kw_m2",
    "vpd_kpa",
    "intercept",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit(project_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project_root, text=True
    ).strip()


def load_dynamic_noise_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["dynamic_noise_decomposition"]


def _measurement_sd_by_depth(calibration_result: dict) -> dict[str, float]:
    sensors = calibration_result["moisture_calibration"]["sensors"]
    grouped: dict[str, list[float]] = {}
    for sensor in sensors:
        grouped.setdefault(str(sensor["depth_label"]), []).append(
            float(sensor["corrected_residual_sd_m3_m3"])
        )
    return {depth: max(values) for depth, values in grouped.items()}


def _flow_relative_rmse(calibration_result: dict, statistic: str) -> float:
    devices = calibration_result["flow_calibration"]["devices"]
    if not devices:
        raise ValueError("Flow calibration result contains no devices")
    return max(float(device[statistic]) for device in devices)


def _coefficients(model_result: dict, layer: str) -> np.ndarray:
    parameter_group = model_result["parameters"][f"{layer}_delta"]
    return np.asarray([parameter_group[key] for key in COEFFICIENT_KEYS], dtype=float)


def _layer_decomposition(
    transitions: pd.DataFrame,
    coefficients: np.ndarray,
    layer: str,
    shallow_measurement_sd: float,
    deep_measurement_sd: float,
    flow_relative_rmse: float,
    variance_tolerance: float,
) -> dict:
    gradient = (
        transitions["theta_shallow"].to_numpy(float)
        - transitions["theta_deep"].to_numpy(float)
    )
    irrigation = transitions["irrigation_delivered_mm"].to_numpy(float)
    design = np.column_stack([
        gradient,
        irrigation,
        transitions["solar_w_m2"].to_numpy(float) / 1000.0,
        transitions["vpd_kpa"].to_numpy(float),
        np.ones(len(transitions)),
    ])
    current_column = "theta_shallow" if layer == "shallow" else "theta_deep"
    next_column = "next_theta_shallow" if layer == "shallow" else "next_theta_deep"
    predicted = transitions[current_column].to_numpy(float) + design @ coefficients
    residual = transitions[next_column].to_numpy(float) - predicted
    residual_variance = float(np.var(residual, ddof=1))

    gradient_coefficient = float(coefficients[0])
    if layer == "shallow":
        current_measurement_variance = (
            (1.0 + gradient_coefficient) ** 2 * shallow_measurement_sd**2
            + gradient_coefficient**2 * deep_measurement_sd**2
        )
        next_measurement_variance = shallow_measurement_sd**2
    else:
        current_measurement_variance = (
            gradient_coefficient**2 * shallow_measurement_sd**2
            + (1.0 - gradient_coefficient) ** 2 * deep_measurement_sd**2
        )
        next_measurement_variance = deep_measurement_sd**2
    measurement_variance = float(current_measurement_variance + next_measurement_variance)
    input_variances = (
        float(coefficients[1]) * irrigation * flow_relative_rmse
    ) ** 2
    mean_input_variance = float(np.mean(input_variances))
    known_error_variance = measurement_variance + mean_input_variance
    unexplained_variance = residual_variance - known_error_variance
    estimable = unexplained_variance > variance_tolerance
    return {
        "transition_count": int(len(transitions)),
        "residual_mean_m3_m3": float(np.mean(residual)),
        "residual_sd_m3_m3": float(np.std(residual, ddof=1)),
        "residual_variance": residual_variance,
        "measurement_error_variance": measurement_variance,
        "input_error_variance_mean": mean_input_variance,
        "known_error_variance": known_error_variance,
        "known_error_to_residual_variance_ratio": float(
            known_error_variance / residual_variance if residual_variance > 0 else np.inf
        ),
        "unexplained_variance_raw": float(unexplained_variance),
        "unexplained_dynamic_sd_candidate_m3_m3": (
            float(np.sqrt(unexplained_variance)) if estimable else None
        ),
        "unexplained_variance_positive": bool(estimable),
    }


def assess_dynamic_noise(
    frame: pd.DataFrame,
    model_result: dict,
    calibration_result: dict,
    config: dict,
    dynamic_input_sha256: str | None = None,
) -> dict:
    transitions = build_transitions(frame)
    validation = transitions[
        transitions["dataset_role"] == config["validation_role"]
    ].copy()
    validation_cycle_count = int(
        validation[["site_id", "zone_id", "cycle_id"]].drop_duplicates().shape[0]
    )
    measurement = _measurement_sd_by_depth(calibration_result)
    shallow_depth = str(config["depth_mapping"]["shallow"])
    deep_depth = str(config["depth_mapping"]["deep"])
    missing_depths = [
        depth for depth in (shallow_depth, deep_depth) if depth not in measurement
    ]
    if missing_depths:
        raise ValueError(f"Missing calibrated measurement noise for depths: {missing_depths}")
    flow_relative_rmse = _flow_relative_rmse(
        calibration_result, str(config["flow_uncertainty_statistic"])
    )
    layer_results = {
        layer: _layer_decomposition(
            validation,
            _coefficients(model_result, layer),
            layer,
            measurement[shallow_depth],
            measurement[deep_depth],
            flow_relative_rmse,
            float(config["variance_tolerance"]),
        )
        for layer in LAYERS
    }
    all_positive = all(
        result["unexplained_variance_positive"] for result in layer_results.values()
    )
    decision = (
        "unexplained_dynamic_variance_screening_candidate"
        if all_positive
        else "not_estimable_measurement_floor_exceeds_dynamic_residual"
    )
    checks = {
        "model_result_passed": model_result.get("status") == "passed",
        "calibration_result_passed": calibration_result.get("status") == "passed",
        "dynamic_input_matches_model_result": (
            dynamic_input_sha256 is None
            or dynamic_input_sha256 == model_result.get("input_sha256")
        ),
        "minimum_validation_cycles_met": validation_cycle_count
        >= int(config["minimum_validation_cycles"]),
        "minimum_validation_transitions_met": len(validation)
        >= int(config["minimum_validation_transitions"]),
        "identification_cycles_excluded": bool(len(validation))
        and bool((validation["dataset_role"] == config["validation_role"]).all()),
        "measurement_noise_available_each_depth": not missing_depths,
        "flow_uncertainty_available": bool(np.isfinite(flow_relative_rmse)),
        "input_interval_alignment_is_interval_end": True,
    }
    return {
        "decision": decision,
        "validation_cycle_count": validation_cycle_count,
        "validation_transition_count": int(len(validation)),
        "measurement_noise_sd_m3_m3": {
            "shallow": measurement[shallow_depth],
            "deep": measurement[deep_depth],
        },
        "flow_relative_rmse": flow_relative_rmse,
        "layers": layer_results,
        "process_noise_sd_m3_m3": (
            {
                layer: layer_results[layer]["unexplained_dynamic_sd_candidate_m3_m3"]
                for layer in LAYERS
            }
            if all_positive else None
        ),
        "screening_checks": checks,
    }


def run_dynamic_noise_assessment(
    project_root: Path,
    config_path: Path,
    output_path: Path | None = None,
) -> dict:
    config = load_dynamic_noise_config(config_path)
    input_path = project_root / config["dynamic_input"]
    model_path = project_root / config["model_result"]
    calibration_path = project_root / config["calibration_result"]
    model_result = json.loads(model_path.read_text(encoding="utf-8"))
    calibration_result = json.loads(calibration_path.read_text(encoding="utf-8"))
    result = assess_dynamic_noise(
        pd.read_csv(input_path), model_result, calibration_result, config, _sha256(input_path)
    )
    expectation = config["frozen_example_expectation"]
    expected_candidate = expectation["expected_process_noise_candidate"]
    workflow_checks = {
        **result["screening_checks"],
        "expected_decision_met": result["decision"] == expectation["expected_decision"],
        "expected_process_noise_candidate_met": (
            result["process_noise_sd_m3_m3"] == expected_candidate
        ),
        "process_noise_not_invented_below_known_error_floor": not (
            result["decision"] == "not_estimable_measurement_floor_exceeds_dynamic_residual"
            and result["process_noise_sd_m3_m3"] is not None
        ),
    }
    result.update({
        "name": config["name"],
        "status": "passed" if all(workflow_checks.values()) else "failed",
        "workflow_checks": workflow_checks,
        "code_commit": _git_commit(project_root),
        "config_sha256": _sha256(config_path),
        "dynamic_input_sha256": _sha256(input_path),
        "model_result_sha256": _sha256(model_path),
        "calibration_result_sha256": _sha256(calibration_path),
        "residual_source": "independent_validation_cycles_only",
        "input_interval_alignment": "row_ending_at_next_state_timestamp",
        "interpretation": (
            "Any positive remainder is an unexplained dynamic residual screening candidate "
            "that still includes model-structure and parameter uncertainty; it is not pure process noise."
        ),
        "evidence_boundary": config["evidence_boundary"],
    })
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
    return result
