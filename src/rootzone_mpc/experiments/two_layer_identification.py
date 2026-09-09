from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rootzone_mpc.data.field_quality import validate_csv
from rootzone_mpc.models.two_layer_greybox import (
    TwoLayerGreyBox,
    bounded_least_squares,
    vapor_pressure_deficit_kpa,
)


STATE_COLUMNS = ["theta_10cm_m3_m3", "theta_25cm_m3_m3"]
CYCLE_KEYS = ["site_id", "zone_id", "cycle_id"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit(project_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project_root, text=True
    ).strip()


def validate_admission_precondition(
    project_root: Path, input_path: Path, cfg: dict
) -> dict | None:
    relative = cfg.get("required_admission_result")
    if not relative:
        return None
    admission_path = project_root / relative
    admission = json.loads(admission_path.read_text(encoding="utf-8"))
    required_decision = cfg["required_admission_decision"]
    if admission.get("workflow_status") != "passed":
        raise ValueError("Required model-admission workflow did not pass")
    if admission.get("model_admission_decision") != required_decision:
        raise ValueError(
            "Required model-admission decision was not met: "
            f"{admission.get('model_admission_decision')} != {required_decision}"
        )
    input_hash = _sha256(input_path)
    if admission.get("input_sha256") != input_hash:
        raise ValueError("Admission result is not bound to the identification input")
    return {
        "path": str(relative),
        "sha256": _sha256(admission_path),
        "decision": admission["model_admission_decision"],
        "input_sha256_matched": True,
    }


def build_transitions(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for keys, cycle in frame.groupby(CYCLE_KEYS, sort=True):
        cycle = cycle.sort_values("timestamp_utc").reset_index(drop=True)
        for index in range(len(cycle) - 1):
            current = cycle.iloc[index]
            following = cycle.iloc[index + 1]
            vpd = float(vapor_pressure_deficit_kpa(
                np.array([following["air_temperature_c"]]),
                np.array([following["relative_humidity_pct"]]),
            )[0])
            rows.append({
                "site_id": keys[0],
                "zone_id": keys[1],
                "cycle_id": keys[2],
                "dataset_role": current["dataset_role"],
                "timestamp_utc": following["timestamp_utc"],
                "theta_shallow": float(current[STATE_COLUMNS[0]]),
                "theta_deep": float(current[STATE_COLUMNS[1]]),
                "next_theta_shallow": float(following[STATE_COLUMNS[0]]),
                "next_theta_deep": float(following[STATE_COLUMNS[1]]),
                "irrigation_delivered_mm": float(following["irrigation_delivered_mm"]),
                "solar_w_m2": float(following["solar_radiation_w_m2"]),
                "vpd_kpa": vpd,
            })
    return pd.DataFrame(rows)


def _design(transitions: pd.DataFrame) -> np.ndarray:
    return np.column_stack([
        transitions["theta_shallow"] - transitions["theta_deep"],
        transitions["irrigation_delivered_mm"],
        transitions["solar_w_m2"] / 1000.0,
        transitions["vpd_kpa"],
        np.ones(len(transitions)),
    ])


def _metrics(observed: np.ndarray, predicted: np.ndarray, baseline: np.ndarray) -> dict:
    residual = predicted - observed
    rmse = float(np.sqrt(np.mean(residual**2)))
    baseline_rmse = float(np.sqrt(np.mean((baseline - observed) ** 2)))
    return {
        "n": int(len(observed)),
        "rmse_m3_m3": rmse,
        "mae_m3_m3": float(np.mean(np.abs(residual))),
        "bias_m3_m3": float(np.mean(residual)),
        "baseline_rmse_m3_m3": baseline_rmse,
        "skill_vs_baseline": float(1.0 - rmse / baseline_rmse) if baseline_rmse > 0 else None,
    }


def _rollout_predictions(frame: pd.DataFrame, role: str, model: TwoLayerGreyBox) -> pd.DataFrame:
    rows = []
    selected = frame[frame["dataset_role"] == role]
    for keys, cycle in selected.groupby(CYCLE_KEYS, sort=True):
        cycle = cycle.sort_values("timestamp_utc").reset_index(drop=True)
        predicted_shallow = float(cycle.loc[0, STATE_COLUMNS[0]])
        predicted_deep = float(cycle.loc[0, STATE_COLUMNS[1]])
        constant_shallow = predicted_shallow
        constant_deep = predicted_deep
        for index in range(1, len(cycle)):
            row = cycle.iloc[index]
            vpd = float(vapor_pressure_deficit_kpa(
                np.array([row["air_temperature_c"]]),
                np.array([row["relative_humidity_pct"]]),
            )[0])
            predicted_shallow, predicted_deep = model.predict_next(
                predicted_shallow, predicted_deep,
                float(row["irrigation_delivered_mm"]),
                float(row["solar_radiation_w_m2"]),
                vpd,
            )
            rows.append({
                "site_id": keys[0], "zone_id": keys[1], "cycle_id": keys[2],
                "dataset_role": role, "timestamp_utc": row["timestamp_utc"],
                "observed_theta_shallow": float(row[STATE_COLUMNS[0]]),
                "observed_theta_deep": float(row[STATE_COLUMNS[1]]),
                "predicted_theta_shallow": predicted_shallow,
                "predicted_theta_deep": predicted_deep,
                "baseline_theta_shallow": constant_shallow,
                "baseline_theta_deep": constant_deep,
            })
    return pd.DataFrame(rows)


def run_two_layer_identification(
    project_root: Path, config_path: Path | None = None
) -> tuple[Path, Path]:
    config_path = config_path or project_root / "configs/two_layer_identification_v1.yaml"
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))["two_layer_identification"]
    input_path = project_root / cfg["input"]
    admission_binding = validate_admission_precondition(project_root, input_path, cfg)
    quality_path = project_root / cfg["quality_config"]
    quality = validate_csv(input_path, quality_path)
    if quality["status"] != "passed":
        raise ValueError(f"Input failed field-data quality gate: {quality['errors']}")

    frame = pd.read_csv(input_path)
    transitions = build_transitions(frame)
    fit_rows = transitions[transitions["dataset_role"] == cfg["identification_role"]].copy()
    validation_rows = transitions[transitions["dataset_role"] == cfg["validation_role"]].copy()
    model_cfg = cfg["model"]
    if list(model_cfg["state_columns"]) != STATE_COLUMNS:
        raise ValueError("Configured state columns do not match the frozen two-layer schema")
    design_fit = _design(fit_rows)
    targets = [
        fit_rows["next_theta_shallow"].to_numpy() - fit_rows["theta_shallow"].to_numpy(),
        fit_rows["next_theta_deep"].to_numpy() - fit_rows["theta_deep"].to_numpy(),
    ]
    bounds = [model_cfg["shallow_delta_bounds"], model_cfg["deep_delta_bounds"]]
    fits = [
        bounded_least_squares(
            design_fit, target,
            np.asarray(bound["lower"]), np.asarray(bound["upper"]),
            max_iterations=int(model_cfg["coordinate_descent_max_iterations"]),
            tolerance=float(model_cfg["coordinate_descent_tolerance"]),
        )
        for target, bound in zip(targets, bounds, strict=True)
    ]
    model = TwoLayerGreyBox(fits[0].coefficients, fits[1].coefficients)

    design_validation = _design(validation_rows)
    predicted_delta = np.column_stack([
        design_validation @ fits[0].coefficients,
        design_validation @ fits[1].coefficients,
    ])
    current = validation_rows[["theta_shallow", "theta_deep"]].to_numpy()
    observed = validation_rows[["next_theta_shallow", "next_theta_deep"]].to_numpy()
    predicted = current + predicted_delta
    one_step_metrics = {
        "theta_10cm": _metrics(observed[:, 0], predicted[:, 0], current[:, 0]),
        "theta_25cm": _metrics(observed[:, 1], predicted[:, 1], current[:, 1]),
    }

    rollout = _rollout_predictions(frame, cfg["validation_role"], model)
    rollout_metrics = {
        "theta_10cm": _metrics(
            rollout["observed_theta_shallow"].to_numpy(),
            rollout["predicted_theta_shallow"].to_numpy(),
            rollout["baseline_theta_shallow"].to_numpy(),
        ),
        "theta_25cm": _metrics(
            rollout["observed_theta_deep"].to_numpy(),
            rollout["predicted_theta_deep"].to_numpy(),
            rollout["baseline_theta_deep"].to_numpy(),
        ),
    }

    criteria = cfg["success_criteria"]
    all_coefficients_within_bounds = all(
        bool(np.all(fit.coefficients >= np.asarray(bound["lower"]))
             and np.all(fit.coefficients <= np.asarray(bound["upper"])))
        for fit, bound in zip(fits, bounds, strict=True)
    )
    validation_cycle_keys = set(map(tuple, frame.loc[
        frame["dataset_role"] == cfg["validation_role"], CYCLE_KEYS
    ].drop_duplicates().to_numpy()))
    fit_cycle_keys = set(map(tuple, frame.loc[
        frame["dataset_role"] == cfg["identification_role"], CYCLE_KEYS
    ].drop_duplicates().to_numpy()))
    invariants = {
        "quality_gate_passed": quality["status"] == "passed",
        "validation_cycles_excluded_from_fit": fit_cycle_keys.isdisjoint(validation_cycle_keys),
        "bounded_solver_converged": all(fit.converged for fit in fits),
        "coefficients_within_physical_sign_bounds": all_coefficients_within_bounds,
        "one_step_skill_threshold_met_each_depth": all(
            value["skill_vs_baseline"] >= float(criteria["minimum_one_step_skill_vs_persistence_each_depth"])
            for value in one_step_metrics.values()
        ),
        "rollout_skill_threshold_met_each_depth": all(
            value["skill_vs_baseline"] >= float(criteria["minimum_rollout_skill_vs_constant_initial_each_depth"])
            for value in rollout_metrics.values()
        ),
        "rollout_bias_limit_met_each_depth": all(
            abs(value["bias_m3_m3"]) <= float(criteria["maximum_absolute_rollout_bias_m3_m3_each_depth"])
            for value in rollout_metrics.values()
        ),
        "rollout_predictions_within_bounds": bool(
            rollout[["predicted_theta_shallow", "predicted_theta_deep"]].min().min()
            >= float(criteria["prediction_lower_bound_m3_m3"])
            and rollout[["predicted_theta_shallow", "predicted_theta_deep"]].max().max()
            <= float(criteria["prediction_upper_bound_m3_m3"])
        ),
    }

    prediction_path = project_root / cfg.get(
        "output_predictions", "outputs/tables/two_layer_validation_predictions_v1.csv"
    )
    prediction_path.parent.mkdir(parents=True, exist_ok=True)
    rollout.to_csv(prediction_path, index=False)
    result = {
        "name": cfg["name"],
        "status": "passed" if all(invariants.values()) else "failed",
        "code_commit": _git_commit(project_root),
        "input_sha256": _sha256(input_path),
        "config_sha256": _sha256(config_path),
        "identification_cycle_count": len(fit_cycle_keys),
        "validation_cycle_count": len(validation_cycle_keys),
        "identification_transition_count": int(len(fit_rows)),
        "validation_transition_count": int(len(validation_rows)),
        "feature_names": model_cfg["feature_names"],
        "parameters": {
            "shallow_delta": dict(zip(model_cfg["feature_names"], fits[0].coefficients.tolist(), strict=True)),
            "deep_delta": dict(zip(model_cfg["feature_names"], fits[1].coefficients.tolist(), strict=True)),
        },
        "solver": {
            "shallow_iterations": fits[0].iterations,
            "deep_iterations": fits[1].iterations,
            "shallow_converged": fits[0].converged,
            "deep_converged": fits[1].converged,
        },
        "one_step_validation": one_step_metrics,
        "rollout_validation": rollout_metrics,
        "invariants": invariants,
        "evidence_boundary": cfg["evidence_boundary"],
        "input_interval_alignment": "row_ending_at_next_state_timestamp",
        "admission_binding": admission_binding,
    }
    result_path = project_root / cfg.get(
        "output_result", "data/processed/two_layer_identification_smoke_v1.json"
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return prediction_path, result_path
