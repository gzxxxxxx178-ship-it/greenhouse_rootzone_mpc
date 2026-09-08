from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rootzone_mpc.data.field_quality import TIMEZONE_SUFFIX


def load_calibration_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["field_calibration_quality"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit(project_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project_root, text=True
    ).strip()


def _linear_fit(predictor: np.ndarray, reference: np.ndarray) -> tuple[float, float]:
    if len(predictor) < 2 or np.ptp(predictor) <= 1e-12:
        return float("nan"), float("nan")
    matrix = np.column_stack([predictor, np.ones(len(predictor))])
    slope, intercept = np.linalg.lstsq(matrix, reference, rcond=None)[0]
    return float(slope), float(intercept)


def _required_columns(frame: pd.DataFrame, required: list[str]) -> tuple[bool, list[str]]:
    missing = sorted(set(required) - set(frame.columns))
    return not missing, missing


def _timestamps_valid(series: pd.Series) -> bool:
    explicit = series.astype(str).map(
        lambda value: bool(TIMEZONE_SUFFIX.search(value))
    ).all()
    parsed = pd.to_datetime(series, utc=True, errors="coerce")
    return bool(explicit and parsed.notna().all())


def _identifiers_nonempty(frame: pd.DataFrame, columns: list[str]) -> bool:
    return bool(frame[columns].apply(
        lambda column: column.notna() & column.astype(str).str.strip().ne("")
    ).all().all())


def evaluate_moisture_calibration(frame: pd.DataFrame, config: dict) -> dict:
    required_ok, missing = _required_columns(frame, config["required_columns"])
    if not required_ok:
        return {"status": "failed", "checks": {"required_columns_present": False},
                "errors": ["missing_columns:" + ",".join(missing)], "sensors": []}
    data = frame.copy()
    keys = ["calibration_batch_id", "sensor_id", "depth_label", "moisture_level_id", "replicate_id"]
    checks = {
        "required_columns_present": True,
        "calibration_keys_unique": not bool(data.duplicated(keys).any()),
        "timestamps_parse_and_include_timezone": _timestamps_valid(data["timestamp_utc"]),
        "identifiers_nonempty": _identifiers_nonempty(data, [
            "calibration_batch_id", "soil_source_id", "sensor_id", "depth_label",
            "moisture_level_id", "replicate_id", "reference_method", "operator_id",
        ]),
    }
    numeric = [
        "sensor_theta_m3_m3", "wet_soil_mass_g", "dry_soil_mass_g", "sample_volume_cm3",
        "water_density_g_cm3",
    ]
    for column in numeric:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    checks["numeric_values_complete"] = bool(data[numeric].notna().all().all())
    data["reference_theta_m3_m3"] = (
        data["wet_soil_mass_g"] - data["dry_soil_mass_g"]
    ) / data["water_density_g_cm3"] / data["sample_volume_cm3"]
    checks["physical_mass_and_volume_valid"] = bool(
        (data["sample_volume_cm3"] > 0).all()
        and (data["water_density_g_cm3"] > 0).all()
        and (data["wet_soil_mass_g"] >= data["dry_soil_mass_g"]).all()
    )
    checks["reference_theta_within_configured_range"] = bool(
        data["reference_theta_m3_m3"].between(
            float(config["reference_theta_min_m3_m3"]),
            float(config["reference_theta_max_m3_m3"]), inclusive="both",
        ).all()
    )

    sensor_rows = []
    group_keys = ["calibration_batch_id", "sensor_id", "depth_label"]
    for (batch, sensor, depth), group in data.groupby(group_keys, sort=True):
        level_counts = group.groupby("moisture_level_id")["replicate_id"].nunique()
        x = group["sensor_theta_m3_m3"].to_numpy(float)
        y = group["reference_theta_m3_m3"].to_numpy(float)
        slope, intercept = _linear_fit(x, y)
        corrected = slope * x + intercept
        residual = corrected - y
        cross_validated = np.full(len(group), np.nan, dtype=float)
        levels = group["moisture_level_id"].astype(str).to_numpy()
        for level in sorted(set(levels)):
            train = levels != level
            test = ~train
            if train.sum() >= 2 and np.ptp(x[train]) > 1e-12:
                fold_slope, fold_intercept = _linear_fit(x[train], y[train])
                cross_validated[test] = fold_slope * x[test] + fold_intercept
        sensor_rows.append({
            "calibration_batch_id": str(batch), "sensor_id": str(sensor),
            "depth_label": str(depth), "row_count": int(len(group)),
            "moisture_level_count": int(len(level_counts)),
            "minimum_replicates_per_level": int(level_counts.min()),
            "reference_span_m3_m3": float(np.ptp(y)),
            "slope": slope, "intercept_m3_m3": intercept,
            "corrected_rmse_m3_m3": float(np.sqrt(np.mean(np.square(residual))))
            if np.isfinite(residual).all() else float("inf"),
            "corrected_residual_sd_m3_m3": float(np.std(residual, ddof=2))
            if len(residual) > 2 and np.isfinite(residual).all() else float("inf"),
            "leave_one_level_out_rmse_m3_m3": float(
                np.sqrt(np.mean(np.square(cross_validated - y)))
            ) if np.isfinite(cross_validated).all() else float("inf"),
        })
    checks["minimum_sensor_depth_combinations_met"] = len(sensor_rows) >= int(
        config["minimum_sensor_depth_combinations"]
    )
    checks["minimum_levels_met"] = bool(sensor_rows) and all(
        row["moisture_level_count"] >= int(config["minimum_moisture_levels_per_sensor"])
        for row in sensor_rows
    )
    checks["minimum_replicates_met"] = bool(sensor_rows) and all(
        row["minimum_replicates_per_level"] >= int(config["minimum_replicates_per_level"])
        for row in sensor_rows
    )
    checks["reference_span_met"] = bool(sensor_rows) and all(
        row["reference_span_m3_m3"] >= float(config["minimum_reference_span_m3_m3"])
        for row in sensor_rows
    )
    checks["calibration_slope_plausible"] = bool(sensor_rows) and all(
        float(config["minimum_calibration_slope"]) <= row["slope"]
        <= float(config["maximum_calibration_slope"]) for row in sensor_rows
    )
    checks["leave_one_level_out_error_within_limit"] = bool(sensor_rows) and all(
        row["leave_one_level_out_rmse_m3_m3"]
        <= float(config["maximum_leave_one_level_out_rmse_m3_m3"])
        for row in sensor_rows
    )
    checks["corrected_error_within_limit"] = bool(sensor_rows) and all(
        row["corrected_rmse_m3_m3"] <= float(config["maximum_corrected_rmse_m3_m3"])
        for row in sensor_rows
    )
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "errors": [name for name, passed in checks.items() if not passed],
        "sensors": sensor_rows,
    }


def evaluate_flow_calibration(frame: pd.DataFrame, config: dict) -> dict:
    required_ok, missing = _required_columns(frame, config["required_columns"])
    if not required_ok:
        return {"status": "failed", "checks": {"required_columns_present": False},
                "errors": ["missing_columns:" + ",".join(missing)], "devices": []}
    data = frame.copy()
    keys = ["calibration_batch_id", "device_id", "operating_point_id", "replicate_id"]
    checks = {
        "required_columns_present": True,
        "calibration_keys_unique": not bool(data.duplicated(keys).any()),
        "timestamps_parse_and_include_timezone": _timestamps_valid(data["timestamp_utc"]),
        "identifiers_nonempty": _identifiers_nonempty(data, [
            "calibration_batch_id", "device_id", "operating_point_id", "replicate_id",
            "reference_method", "operator_id",
        ]),
    }
    numeric = [
        "duration_s", "initial_container_mass_kg", "final_container_mass_kg",
        "water_density_kg_l", "device_reported_volume_l",
    ]
    for column in numeric:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    checks["numeric_values_complete"] = bool(data[numeric].notna().all().all())
    data["reference_volume_l"] = (
        data["final_container_mass_kg"] - data["initial_container_mass_kg"]
    ) / data["water_density_kg_l"]
    checks["physical_mass_duration_and_density_valid"] = bool(
        (data["duration_s"] > 0).all()
        and (data["water_density_kg_l"] > 0).all()
        and (data["final_container_mass_kg"] > data["initial_container_mass_kg"]).all()
        and (data["device_reported_volume_l"] > 0).all()
    )

    device_rows = []
    for (batch, device), group in data.groupby(["calibration_batch_id", "device_id"], sort=True):
        point_counts = group.groupby("operating_point_id")["replicate_id"].nunique()
        x = group["device_reported_volume_l"].to_numpy(float)
        y = group["reference_volume_l"].to_numpy(float)
        slope, intercept = _linear_fit(x, y)
        corrected = slope * x + intercept
        relative_error = (corrected - y) / y
        cross_validated = np.full(len(group), np.nan, dtype=float)
        points = group["operating_point_id"].astype(str).to_numpy()
        for point in sorted(set(points)):
            train = points != point
            test = ~train
            if train.sum() >= 2 and np.ptp(x[train]) > 1e-12:
                fold_slope, fold_intercept = _linear_fit(x[train], y[train])
                cross_validated[test] = fold_slope * x[test] + fold_intercept
        cross_relative = (cross_validated - y) / y
        device_rows.append({
            "calibration_batch_id": str(batch), "device_id": str(device),
            "row_count": int(len(group)), "operating_point_count": int(len(point_counts)),
            "minimum_replicates_per_point": int(point_counts.min()),
            "reference_volume_span_l": float(np.ptp(y)),
            "slope": slope, "intercept_l": intercept,
            "corrected_relative_rmse": float(np.sqrt(np.mean(np.square(relative_error))))
            if np.isfinite(relative_error).all() else float("inf"),
            "corrected_maximum_absolute_relative_error": float(np.max(np.abs(relative_error)))
            if np.isfinite(relative_error).all() else float("inf"),
            "leave_one_point_out_relative_rmse": float(
                np.sqrt(np.mean(np.square(cross_relative)))
            ) if np.isfinite(cross_relative).all() else float("inf"),
        })
    checks["minimum_devices_met"] = len(device_rows) >= int(config["minimum_devices"])
    checks["minimum_operating_points_met"] = bool(device_rows) and all(
        row["operating_point_count"] >= int(config["minimum_operating_points_per_device"])
        for row in device_rows
    )
    checks["minimum_replicates_met"] = bool(device_rows) and all(
        row["minimum_replicates_per_point"] >= int(config["minimum_replicates_per_point"])
        for row in device_rows
    )
    checks["reference_volume_span_met"] = bool(device_rows) and all(
        row["reference_volume_span_l"] >= float(config["minimum_reference_volume_span_l"])
        for row in device_rows
    )
    checks["calibration_slope_plausible"] = bool(device_rows) and all(
        float(config["minimum_calibration_slope"]) <= row["slope"]
        <= float(config["maximum_calibration_slope"]) for row in device_rows
    )
    checks["leave_one_point_out_error_within_limit"] = bool(device_rows) and all(
        row["leave_one_point_out_relative_rmse"]
        <= float(config["maximum_leave_one_point_out_relative_rmse"])
        for row in device_rows
    )
    checks["corrected_relative_error_within_limit"] = bool(device_rows) and all(
        row["corrected_relative_rmse"] <= float(config["maximum_corrected_relative_rmse"])
        and row["corrected_maximum_absolute_relative_error"]
        <= float(config["maximum_corrected_absolute_relative_error"])
        for row in device_rows
    )
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "errors": [name for name, passed in checks.items() if not passed],
        "devices": device_rows,
    }


def run_calibration_assessment(
    moisture_path: Path, flow_path: Path, config_path: Path, project_root: Path,
    output_path: Path | None = None,
) -> dict:
    config = load_calibration_config(config_path)
    moisture = evaluate_moisture_calibration(
        pd.read_csv(moisture_path), config["moisture"]
    )
    flow = evaluate_flow_calibration(pd.read_csv(flow_path), config["flow"])
    passed = moisture["status"] == "passed" and flow["status"] == "passed"
    residual_sds = [row["corrected_residual_sd_m3_m3"] for row in moisture["sensors"]]
    handoff = {
        "measurement_noise_sd_m3_m3": max(residual_sds) if residual_sds else None,
        "flow_corrected_maximum_absolute_relative_error": max(
            (row["corrected_maximum_absolute_relative_error"] for row in flow["devices"]),
            default=None,
        ),
        "process_noise_sd_m3_m3": None,
        "information_gate_status": (
            "measurement_noise_ready_process_noise_pending" if passed
            else "calibration_quality_failed"
        ),
        "field_information_threshold_ready": False,
    }
    expectation = config["frozen_example_expectation"]
    is_frozen_example = (
        moisture_path.resolve() == (project_root / expectation["moisture_input"]).resolve()
        and flow_path.resolve() == (project_root / expectation["flow_input"]).resolve()
    )
    workflow_checks = {
        "component_quality_passed": passed,
        "expected_example_status_met": (
            not is_frozen_example or expectation["expected_status"] == "passed" == (
                "passed" if passed else "failed"
            )
        ),
        "expected_handoff_status_met": (
            not is_frozen_example
            or handoff["information_gate_status"]
            == expectation["expected_information_gate_status"]
        ),
        "process_noise_not_inferred_from_calibration": handoff["process_noise_sd_m3_m3"] is None,
        "field_information_threshold_not_prematurely_ready": not handoff[
            "field_information_threshold_ready"
        ],
    }
    result = {
        "name": config["name"],
        "status": "passed" if all(workflow_checks.values()) else "failed",
        "code_commit": _git_commit(project_root),
        "config_sha256": _sha256(config_path),
        "moisture_input_sha256": _sha256(moisture_path),
        "flow_input_sha256": _sha256(flow_path),
        "moisture_calibration": moisture,
        "flow_calibration": flow,
        "model_admission_handoff": handoff,
        "workflow_checks": workflow_checks,
        "evidence_boundary": config["evidence_boundary"],
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
