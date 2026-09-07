from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


ROLE_IDENTIFICATION = "identification"
ROLE_VALIDATION = "validation"
ALLOWED_ROLES = {ROLE_IDENTIFICATION, ROLE_VALIDATION}
TIMEZONE_SUFFIX = re.compile(r"(?:Z|[+-]\d{2}:\d{2})$")


def load_quality_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["field_data_quality"]


def _check_range(series: pd.Series, low: float, high: float) -> bool:
    observed = pd.to_numeric(series, errors="coerce").dropna()
    return bool(len(observed) and observed.between(low, high, inclusive="both").all())


def validate_field_data(frame: pd.DataFrame, config: dict) -> dict:
    required = list(config["required_columns"])
    missing_columns = sorted(set(required) - set(frame.columns))
    checks: dict[str, bool] = {"required_columns_present": not missing_columns}
    errors: list[str] = []
    if missing_columns:
        errors.append("missing_columns:" + ",".join(missing_columns))
        return {
            "status": "failed", "row_count": int(len(frame)), "checks": checks,
            "errors": errors, "cycle_summary": [],
        }

    data = frame.copy()
    timezone_explicit = data["timestamp_utc"].astype(str).map(
        lambda value: bool(TIMEZONE_SUFFIX.search(value))
    ).all()
    timestamps = pd.to_datetime(data["timestamp_utc"], utc=True, errors="coerce")
    checks["timestamps_parse_and_include_timezone"] = bool(timezone_explicit and timestamps.notna().all())
    data["_timestamp"] = timestamps

    key_columns = ["site_id", "zone_id", "timestamp_utc"]
    checks["observation_keys_unique"] = not bool(data.duplicated(key_columns).any())
    checks["identifiers_nonempty"] = bool(
        data[["site_id", "zone_id", "cycle_id"]]
        .apply(lambda column: column.notna() & column.astype(str).str.strip().ne(""))
        .all().all()
    )
    checks["dataset_roles_valid"] = set(data["dataset_role"].dropna().astype(str)) <= ALLOWED_ROLES

    cycle_keys = ["site_id", "zone_id", "cycle_id"]
    cycle_role_counts = data.groupby(cycle_keys)["dataset_role"].nunique(dropna=False)
    checks["cycles_do_not_cross_roles"] = bool(len(cycle_role_counts) and (cycle_role_counts == 1).all())
    role_cycles = data.drop_duplicates(cycle_keys).groupby("dataset_role").size().to_dict()
    checks["minimum_identification_cycles"] = (
        int(role_cycles.get(ROLE_IDENTIFICATION, 0))
        >= int(config["minimum_identification_cycles"])
    )
    checks["minimum_validation_cycles"] = (
        int(role_cycles.get(ROLE_VALIDATION, 0)) >= int(config["minimum_validation_cycles"])
    )

    numeric_columns = [
        "sample_interval_minutes", "zone_area_m2", "theta_10cm_m3_m3",
        "theta_25cm_m3_m3", "irrigation_command_mm", "irrigation_delivered_mm",
        "flow_l_min", "cumulative_water_l", "air_temperature_c",
        "relative_humidity_pct", "solar_radiation_w_m2", "root_depth_m",
    ]
    for column in numeric_columns:
        data[column] = pd.to_numeric(data[column], errors="coerce")

    max_missing = float(config["maximum_missing_fraction"])
    core_sensors = [
        "theta_10cm_m3_m3", "theta_25cm_m3_m3", "flow_l_min",
        "air_temperature_c", "relative_humidity_pct", "solar_radiation_w_m2",
    ]
    missing_fractions = {column: float(data[column].isna().mean()) for column in core_sensors}
    checks["core_sensor_coverage"] = all(value <= max_missing for value in missing_fractions.values())
    checks["irrigation_accounting_complete"] = bool(
        data[[
            "sample_interval_minutes", "zone_area_m2", "irrigation_command_mm",
            "irrigation_delivered_mm", "flow_l_min", "cumulative_water_l",
        ]].notna().all().all()
    )
    checks["sample_interval_within_limit"] = bool(
        data["sample_interval_minutes"].notna().all()
        and data["sample_interval_minutes"].between(
            0, float(config["maximum_sample_interval_minutes"]), inclusive="right"
        ).all()
    )

    theta_low = float(config["theta_min_m3_m3"])
    theta_high = float(config["theta_max_m3_m3"])
    checks["soil_moisture_within_physical_bounds"] = all(
        _check_range(data[column], theta_low, theta_high)
        for column in ["theta_10cm_m3_m3", "theta_25cm_m3_m3"]
    )
    reference_observations = pd.concat([
        pd.to_numeric(data["reference_theta_10cm_m3_m3"], errors="coerce"),
        pd.to_numeric(data["reference_theta_25cm_m3_m3"], errors="coerce"),
    ]).dropna()
    checks["reference_moisture_within_physical_bounds"] = bool(
        len(reference_observations)
        and reference_observations.between(theta_low, theta_high, inclusive="both").all()
    )
    checks["environment_and_irrigation_ranges_valid"] = bool(
        _check_range(data["relative_humidity_pct"], 0, 100)
        and (data["sample_interval_minutes"].dropna() > 0).all()
        and (data["zone_area_m2"].dropna() > 0).all()
        and (data["root_depth_m"].dropna() > 0).all()
        and (data["irrigation_command_mm"].dropna() >= 0).all()
        and (data["irrigation_delivered_mm"].dropna() >= 0).all()
        and (data["flow_l_min"].dropna() >= 0).all()
        and (data["cumulative_water_l"].dropna() >= 0).all()
        and (data["solar_radiation_w_m2"].dropna() >= 0).all()
        and data["air_temperature_c"].dropna().between(-20, 60, inclusive="both").all()
    )

    cycle_summary: list[dict] = []
    gap_ok = True
    wet_dry_ok = True
    reference_ok = True
    water_balance_ok = True
    monotonic_meter_ok = True
    for (site_id, zone_id, cycle_id), cycle in data.groupby(cycle_keys, sort=True):
        cycle = cycle.sort_values("_timestamp")
        nominal_minutes = float(cycle["sample_interval_minutes"].median())
        gaps = cycle["_timestamp"].diff().dt.total_seconds().dropna() / 60
        cycle_gap_ok = bool(len(gaps) and gaps.max() <= nominal_minutes * float(config["maximum_gap_factor"]))

        depth_dynamics = {}
        cycle_wet_dry_ok = True
        for depth in ["theta_10cm_m3_m3", "theta_25cm_m3_m3"]:
            values = cycle[depth].dropna().to_numpy(float)
            peak_index = int(np.argmax(values)) if len(values) else 0
            wetting_rise = float(values[peak_index] - values[0]) if len(values) else math.nan
            drydown_fall = float(values[peak_index] - values[-1]) if len(values) else math.nan
            depth_dynamics[depth] = {
                "wetting_rise_m3_m3": wetting_rise,
                "drydown_fall_m3_m3": drydown_fall,
            }
            cycle_wet_dry_ok &= bool(
                wetting_rise >= float(config["minimum_wetting_rise_m3_m3"])
                and drydown_fall >= float(config["minimum_drydown_fall_m3_m3"])
            )

        min_reference = int(config["minimum_reference_samples_per_depth_per_cycle"])
        reference_counts = {
            depth: int(cycle[depth].notna().sum())
            for depth in ["reference_theta_10cm_m3_m3", "reference_theta_25cm_m3_m3"]
        }
        cycle_reference_ok = min(reference_counts.values()) >= min_reference

        meter = cycle["cumulative_water_l"].dropna()
        cycle_meter_ok = bool(len(meter) and meter.diff().dropna().ge(-1e-9).all())
        meter_increment_l = float(meter.iloc[-1] - meter.iloc[0]) if len(meter) else math.nan
        delivered_l = float((cycle["irrigation_delivered_mm"] * cycle["zone_area_m2"]).sum())
        flow_l = float((cycle["flow_l_min"] * cycle["sample_interval_minutes"]).sum())
        scale = max(delivered_l, flow_l, 1e-9)
        relative_water_error = abs(delivered_l - flow_l) / scale
        meter_scale = max(delivered_l, meter_increment_l, 1e-9)
        relative_meter_error = abs(delivered_l - meter_increment_l) / meter_scale
        cycle_water_ok = bool(
            delivered_l > 0
            and relative_water_error <= float(config["water_balance_relative_tolerance"])
            and relative_meter_error <= float(config["water_balance_relative_tolerance"])
        )

        gap_ok &= cycle_gap_ok
        wet_dry_ok &= cycle_wet_dry_ok
        reference_ok &= cycle_reference_ok
        water_balance_ok &= cycle_water_ok
        monotonic_meter_ok &= cycle_meter_ok
        cycle_summary.append({
            "site_id": str(site_id),
            "zone_id": str(zone_id),
            "cycle_id": str(cycle_id),
            "dataset_role": str(cycle["dataset_role"].iloc[0]),
            "row_count": int(len(cycle)),
            "depth_dynamics": depth_dynamics,
            "reference_samples_per_depth": reference_counts,
            "delivered_volume_l": delivered_l,
            "flow_integrated_volume_l": flow_l,
            "cumulative_meter_increment_l": meter_increment_l,
            "relative_water_error": relative_water_error,
            "relative_meter_error": relative_meter_error,
            "gap_check_passed": cycle_gap_ok,
        })

    checks["sampling_gaps_within_limit"] = gap_ok
    checks["complete_wetting_drydown_cycles"] = wet_dry_ok
    checks["reference_samples_sufficient"] = reference_ok
    checks["flow_matches_delivered_water"] = water_balance_ok
    checks["cumulative_meter_monotonic_within_cycle"] = monotonic_meter_ok
    checks["sensor_status_accepted"] = bool(data["sensor_status"].isin(["ok", "calibration_check"]).all())

    for name, passed in checks.items():
        if not passed:
            errors.append(name)
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "row_count": int(len(data)),
        "cycle_counts": {
            ROLE_IDENTIFICATION: int(role_cycles.get(ROLE_IDENTIFICATION, 0)),
            ROLE_VALIDATION: int(role_cycles.get(ROLE_VALIDATION, 0)),
        },
        "missing_fractions": missing_fractions,
        "checks": checks,
        "errors": errors,
        "cycle_summary": cycle_summary,
    }


def validate_csv(input_path: Path, config_path: Path, output_path: Path | None = None) -> dict:
    result = validate_field_data(pd.read_csv(input_path), load_quality_config(config_path))
    result["input_path"] = str(input_path)
    result["schema_version"] = load_quality_config(config_path)["schema_version"]
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
