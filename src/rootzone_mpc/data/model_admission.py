from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rootzone_mpc.data.field_quality import load_quality_config, validate_field_data
from rootzone_mpc.experiments.information_identifiability import amplitude_sensitive_information


IDENTIFICATION = "identification"
DESIGN_COLUMNS = [
    "theta_shallow_minus_deep", "irrigation_delivered_mm",
    "solar_kw_m2", "vpd_kpa", "intercept",
]


def load_admission_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["field_model_admission"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit(project_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project_root, text=True
    ).strip()


def saturation_vapour_pressure_kpa(temperature_c: np.ndarray) -> np.ndarray:
    temperature = np.asarray(temperature_c, dtype=float)
    return 0.6108 * np.exp(17.27 * temperature / (temperature + 237.3))


def field_identification_design(frame: pd.DataFrame) -> tuple[np.ndarray, dict]:
    identification = frame[frame["dataset_role"] == IDENTIFICATION].copy()
    cycle_keys = ["site_id", "zone_id", "cycle_id"]
    rows: list[list[float]] = []
    possible_transitions = 0
    for _, cycle in identification.groupby(cycle_keys, sort=True):
        cycle = cycle.copy()
        cycle["_timestamp"] = pd.to_datetime(cycle["timestamp_utc"], utc=True, errors="coerce")
        cycle = cycle.sort_values("_timestamp")
        possible_transitions += max(len(cycle) - 1, 0)
        numeric = [
            "theta_10cm_m3_m3", "theta_25cm_m3_m3", "irrigation_delivered_mm",
            "solar_radiation_w_m2", "air_temperature_c", "relative_humidity_pct",
        ]
        for column in numeric:
            cycle[column] = pd.to_numeric(cycle[column], errors="coerce")
        temperature = cycle["air_temperature_c"].to_numpy(float)
        humidity = cycle["relative_humidity_pct"].to_numpy(float)
        vpd = saturation_vapour_pressure_kpa(temperature) * (1.0 - humidity / 100.0)
        for step in range(max(len(cycle) - 1, 0)):
            current = cycle.iloc[step]
            following = cycle.iloc[step + 1]
            values = np.asarray([
                current["theta_10cm_m3_m3"], current["theta_25cm_m3_m3"],
                following["theta_10cm_m3_m3"], following["theta_25cm_m3_m3"],
                following["irrigation_delivered_mm"], following["solar_radiation_w_m2"],
                vpd[step + 1],
            ], dtype=float)
            if not np.isfinite(values).all():
                continue
            rows.append([
                values[0] - values[1], values[4], values[5] / 1000.0, values[6], 1.0,
            ])
    design = np.asarray(rows, dtype=float)
    if not len(design):
        design = np.empty((0, len(DESIGN_COLUMNS)), dtype=float)
    diagnostics = {
        "identification_cycle_count": int(
            identification.drop_duplicates(cycle_keys).shape[0]
        ),
        "possible_transition_count": int(possible_transitions),
        "usable_transition_count": int(len(design)),
        "usable_transition_fraction": float(
            len(design) / possible_transitions if possible_transitions else 0.0
        ),
        "design_columns": DESIGN_COLUMNS,
        "input_interval_alignment": "row_ending_at_next_state_timestamp",
        "design_rank": int(np.linalg.matrix_rank(design)) if len(design) else 0,
    }
    return design, diagnostics


def _parameter_widths(config: dict, layer: str) -> np.ndarray:
    bounds = config["parameter_bounds"][layer]
    return np.asarray(bounds["upper"], dtype=float) - np.asarray(bounds["lower"], dtype=float)


def assess_model_admission(
    frame: pd.DataFrame, quality_config: dict, admission_config: dict
) -> dict:
    quality = validate_field_data(frame, quality_config)
    if quality["status"] != "passed":
        return {
            "model_admission_decision": "blocked_data_quality",
            "quality_status": quality["status"],
            "quality_failed_checks": quality["errors"],
            "information_assessment": None,
            "admission_checks": {"base_data_quality_passed": False},
        }

    design, diagnostics = field_identification_design(frame)
    reference = admission_config["information_reference"]
    delta_noise_sd = float(np.sqrt(
        2 * float(reference["measurement_noise_sd_m3_m3"]) ** 2
        + float(reference["process_noise_sd_m3_m3"]) ** 2
    ))
    layer_information = {
        layer: amplitude_sensitive_information(
            design, _parameter_widths(admission_config, layer), delta_noise_sd
        )
        for layer in ["shallow_delta", "deep_delta"]
    }
    minimum_information = min(
        item["minimum_singular_value_snr"] for item in layer_information.values()
    )
    combined_uncertainty = float(np.sqrt(np.mean([
        item["predicted_normalized_se_rms"] ** 2 for item in layer_information.values()
    ])))
    checks = {
        "base_data_quality_passed": True,
        "usable_identification_transitions_sufficient": diagnostics["usable_transition_count"]
        >= int(admission_config["minimum_usable_identification_transitions"]),
        "usable_transition_fraction_sufficient": diagnostics["usable_transition_fraction"]
        >= float(admission_config["minimum_usable_transition_fraction"]),
        "design_matrix_full_rank": diagnostics["design_rank"]
        >= int(admission_config["required_design_rank"]),
        "information_strength_sufficient": minimum_information
        >= float(reference["minimum_information_singular_value_snr"]),
        "predicted_uncertainty_sufficient": combined_uncertainty
        <= float(reference["maximum_predicted_normalized_se_rms"]),
    }
    decision = (
        "provisional_ready_for_identification"
        if all(checks.values()) else "collect_more_excitation"
    )
    return {
        "model_admission_decision": decision,
        "quality_status": quality["status"],
        "quality_failed_checks": [],
        "information_assessment": {
            **diagnostics,
            "assumed_delta_observation_noise_sd_m3_m3": delta_noise_sd,
            "layer_information": layer_information,
            "minimum_information_singular_value_snr": minimum_information,
            "combined_predicted_normalized_se_rms": combined_uncertainty,
            "reference_calibration_status": reference["calibration_status"],
        },
        "admission_checks": checks,
    }


def assess_csv(
    input_path: Path, admission_config_path: Path, project_root: Path,
    output_path: Path | None = None,
) -> dict:
    admission_config = load_admission_config(admission_config_path)
    quality_path = project_root / admission_config["quality_config"]
    result = assess_model_admission(
        pd.read_csv(input_path), load_quality_config(quality_path), admission_config
    )
    expectation = admission_config.get("frozen_example_expectation", {})
    expected_input = project_root / expectation.get("input", "")
    is_frozen_example = input_path.resolve() == expected_input.resolve()
    workflow_checks = {
        "expected_quality_status_met": (
            not is_frozen_example
            or result["quality_status"] == expectation["expected_quality_status"]
        ),
        "expected_admission_decision_met": (
            not is_frozen_example
            or result["model_admission_decision"]
            == expectation["expected_model_admission_decision"]
        ),
    }
    result.update({
        "name": admission_config["name"],
        "workflow_status": "passed" if all(workflow_checks.values()) else "failed",
        "workflow_checks": workflow_checks,
        "input_path": str(input_path),
        "input_schema_version": admission_config["input_schema_version"],
        "model_structure": admission_config["model_structure"],
        "code_commit": _git_commit(project_root),
        "config_sha256": _sha256(admission_config_path),
        "evidence_boundary": admission_config["evidence_boundary"],
    })
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
