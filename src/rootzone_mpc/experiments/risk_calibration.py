from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rootzone_mpc.controllers import InternalModel
from rootzone_mpc.experiments.development import (
    _controller,
    _plant_from_row,
    build_et_series,
)


def _normal_features(row: pd.Series, tuning: dict, frozen: dict, anomaly: dict) -> pd.DataFrame:
    plant = _plant_from_row(row)
    controller = _controller("mpc", frozen["mpc"]["parameters"], tuning)
    internal = InternalModel(**frozen["internal_model"])
    hours = int(row["horizon_hours"])
    horizon = int(frozen["mpc"]["parameters"]["prediction_horizon"])
    actual_et = build_et_series(row, hours + horizon)
    forecast_rng = np.random.default_rng(int(row["scenario_seed"]) + 303)
    flow_rng = np.random.default_rng(int(row["scenario_seed"]) + 505)
    forecast_noise = forecast_rng.normal(
        0.0, float(row["forecast_noise_std_fraction"]), size=(hours, horizon)
    )
    flow_noise_std = float(
        anomaly["risk_calibration"]["simulated_flow_feedback_noise_std_fraction"]
    )
    alpha = float(anomaly["risk_calibration"]["residual_ewma_alpha"])
    observation = plant.measure()
    residual_ewma = 0.0
    rows = []
    for step in range(hours):
        forecast = np.maximum(
            actual_et[step : step + horizon]
            * (1.0 + float(row["forecast_bias_fraction"]))
            * (1.0 + forecast_noise[step]),
            0.0,
        )
        action = controller.act(observation, forecast.tolist())
        prediction = internal.predict_step(observation, action, float(forecast[0]))
        result = plant.step(action, float(actual_et[step]))
        next_observation = result.theta_measured
        residual = abs(next_observation - prediction)
        residual_ewma = alpha * residual + (1.0 - alpha) * residual_ewma
        observation_rate = abs(next_observation - observation)
        delivered_feedback = action * (1.0 + flow_rng.normal(0.0, flow_noise_std))
        execution_error = abs(delivered_feedback - action) / max(abs(action), 1.0)
        rows.append(
            {
                "scenario_id": row["scenario_id"],
                "step": step,
                "observation_rate": observation_rate,
                "model_residual_ewma": residual_ewma,
                "execution_relative_error": execution_error,
                "command_active": bool(action > 0.0),
            }
        )
        observation = next_observation
    return pd.DataFrame(rows)


def _scale(values: pd.Series, low_q: float, high_q: float, minimum_width: float) -> dict:
    low = float(values.quantile(low_q))
    high = float(values.quantile(high_q))
    high = max(high, low + minimum_width)
    return {
        "low": low,
        "high": high,
        "low_quantile": low_q,
        "high_quantile": high_q,
        "sample_count": int(len(values)),
    }


def calibrate_risk_scales(project_root: Path) -> tuple[Path, Path]:
    anomaly_path = project_root / "configs" / "anomaly_design_v1.yaml"
    tuning_path = project_root / "configs" / "tuning_design_v1.yaml"
    frozen_path = project_root / "configs" / "frozen_controller_v1.yaml"
    scenario_path = project_root / "data" / "processed" / "scenario_manifest_v1.csv"
    anomaly = yaml.safe_load(anomaly_path.read_text(encoding="utf-8"))
    tuning = yaml.safe_load(tuning_path.read_text(encoding="utf-8"))
    frozen = yaml.safe_load(frozen_path.read_text(encoding="utf-8"))
    scenarios = pd.read_csv(scenario_path)
    source_split = anomaly["risk_calibration"]["source_split"]
    development = scenarios[scenarios["split"] == source_split]
    if len(development) != 48 or (development["split"] != "development").any():
        raise ValueError("Risk calibration requires exactly 48 development scenarios")
    features = pd.concat(
        [_normal_features(row, tuning, frozen, anomaly) for _, row in development.iterrows()],
        ignore_index=True,
    )
    calibration = anomaly["risk_calibration"]
    low_q = float(calibration["low_quantile"])
    high_q = float(calibration["high_quantile"])
    minimum_width = float(calibration["minimum_scale_width"])
    active = features[features["command_active"]]
    scales = {
        "observation_rate": _scale(features["observation_rate"], low_q, high_q, minimum_width),
        "model_residual_ewma": _scale(
            features["model_residual_ewma"], low_q, high_q, minimum_width
        ),
        "execution_relative_error": _scale(
            active["execution_relative_error"], low_q, high_q, minimum_width
        ),
    }
    output_dir = project_root / "outputs" / "runs"
    output_dir.mkdir(parents=True, exist_ok=True)
    feature_path = output_dir / "risk_calibration_normal_features.csv"
    features.to_csv(feature_path, index=False)
    frozen_supervision_path = project_root / "configs" / "frozen_supervision_v1.yaml"
    payload = {
        "freeze_name": "frozen_supervision_v1",
        "calibration_split": "development",
        "calibration_condition": "normal_operation_only",
        "locked_evaluation_scenarios_used": 0,
        "risk_scales": scales,
        "monitor": {
            "residual_ewma_alpha": calibration["residual_ewma_alpha"],
            "maximum_observation_age_steps": 2,
            "valid_theta_min": 0.08,
            "valid_theta_max": 0.45,
        },
        "supervisor": {
            "risk_high": 0.70,
            "risk_recovery": 0.40,
            "minimum_dwell_steps": 4,
            "recovery_confirmation_steps": 6,
        },
    }
    frozen_supervision_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    audit_path = project_root / "data" / "processed" / "risk_calibration_v1.json"
    audit = {
        "development_scenarios_used": int(len(development)),
        "locked_evaluation_scenarios_used": 0,
        "normal_feature_rows": int(len(features)),
        "active_command_feature_rows": int(len(active)),
        "risk_scales": scales,
        "hashes": {
            "anomaly_config": hashlib.sha256(anomaly_path.read_bytes()).hexdigest(),
            "tuning_config": hashlib.sha256(tuning_path.read_bytes()).hexdigest(),
            "frozen_controller": hashlib.sha256(frozen_path.read_bytes()).hexdigest(),
            "scenario_manifest": hashlib.sha256(scenario_path.read_bytes()).hexdigest(),
            "normal_features": hashlib.sha256(feature_path.read_bytes()).hexdigest(),
        },
        "evidence_boundary": "normal development simulations only",
    }
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return frozen_supervision_path, audit_path
