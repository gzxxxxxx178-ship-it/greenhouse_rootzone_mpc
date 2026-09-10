from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rootzone_mpc.controllers.two_layer import (
    TwoLayerMPC,
    TwoLayerMPCParameters,
    TwoLayerRuleController,
    TwoLayerRuleParameters,
)
from rootzone_mpc.design.scenario_manifest import _latin_hypercube
from rootzone_mpc.experiments.two_layer_bridge_data import _weather, load_protocol
from rootzone_mpc.models.two_layer_greybox import TwoLayerGreyBox
from rootzone_mpc.models.two_layer_plant import (
    TwoLayerPlantParameters,
    TwoLayerRootZonePlant,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit(project_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project_root, text=True
    ).strip()


def _parameter_bounds(nominal: float, fraction: float) -> tuple[float, float]:
    return nominal * (1.0 - fraction), nominal * (1.0 + fraction)


def _scenario_split(
    split: str, prefix: str, count: int, seed: int, protocol: dict
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    nominal = protocol["plant_nominal"]
    envelope = protocol["plant_parameter_envelope_fraction"]
    varying = list(envelope)
    extra_bounds = {
        "initial_theta_shallow": (0.195, 0.245),
        "initial_theta_deep": (0.200, 0.250),
        "solar_multiplier": (0.80, 1.20),
        "vpd_additive_kpa": (-0.15, 0.20),
        "forecast_solar_bias_fraction": (-0.20, 0.20),
        "forecast_vpd_bias_kpa": (-0.20, 0.20),
    }
    names = varying + list(extra_bounds)
    samples = _latin_hypercube(count, len(names), rng)
    values: dict[str, np.ndarray] = {}
    for index, name in enumerate(varying):
        low, high = _parameter_bounds(float(nominal[name]), float(envelope[name]))
        values[name] = low + samples[:, index] * (high - low)
    for offset, (name, (low, high)) in enumerate(extra_bounds.items(), start=len(varying)):
        values[name] = low + samples[:, offset] * (high - low)
    frame = pd.DataFrame(values)
    frame.insert(0, "scenario_seed", seed * 1000 + np.arange(1, count + 1))
    frame.insert(0, "split", split)
    frame.insert(0, "scenario_id", [f"{prefix}{index:03d}" for index in range(1, count + 1)])
    frame["weather_phase_index"] = rng.integers(0, 9, size=count)
    return frame


def build_two_layer_control_scenarios(protocol: dict) -> tuple[pd.DataFrame, dict]:
    development = protocol["development_design"]
    locked = protocol["locked_design"]
    first = _scenario_split(
        "development", "TD", int(development["scenario_count"]),
        int(development["master_seed"]), protocol,
    )
    second = _scenario_split(
        "locked_evaluation", "TE", int(locked["scenario_count"]),
        int(locked["master_seed"]), protocol,
    )
    frame = pd.concat([first, second], ignore_index=True)
    checks = {
        "split_counts_match": frame.groupby("split").size().to_dict() == {
            "development": int(development["scenario_count"]),
            "locked_evaluation": int(locked["scenario_count"]),
        },
        "scenario_ids_unique": bool(frame["scenario_id"].is_unique),
        "scenario_seeds_unique": bool(frame["scenario_seed"].is_unique),
        "initial_states_inside_control_safety_bounds": bool(
            frame["initial_theta_shallow"].between(0.14, 0.32).all()
            and frame["initial_theta_deep"].between(0.15, 0.33).all()
        ),
        "physical_fractions_valid": bool(
            frame["irrigation_efficiency"].between(0, 1).all()
            and frame["bypass_fraction"].between(0, 1).all()
            and frame["shallow_et_fraction"].between(0, 1).all()
        ),
    }
    return frame, {"status": "passed" if all(checks.values()) else "failed", "checks": checks}


def _load_fitted_model(model_result: dict) -> TwoLayerGreyBox:
    feature_names = model_result["feature_names"]
    shallow = np.asarray([
        model_result["parameters"]["shallow_delta"][name] for name in feature_names
    ])
    deep = np.asarray([
        model_result["parameters"]["deep_delta"][name] for name in feature_names
    ])
    return TwoLayerGreyBox(shallow, deep)


def _plant_parameters(row: pd.Series, protocol: dict) -> TwoLayerPlantParameters:
    nominal = dict(protocol["plant_nominal"])
    for name in protocol["plant_parameter_envelope_fraction"]:
        nominal[name] = float(row[name])
    noise = protocol["synthetic_noise_reference"]
    nominal.update({
        "process_noise_sd_m3_m3": float(noise["process_noise_sd_m3_m3"]),
        "measurement_noise_sd_m3_m3": float(noise["measurement_noise_sd_m3_m3"]),
        "command_to_delivered_relative_sd": float(noise["command_to_delivered_relative_sd"]),
    })
    return TwoLayerPlantParameters(**nominal)


def _controller(kind: str, candidate: dict, protocol: dict, model: TwoLayerGreyBox):
    if kind == "rule":
        return TwoLayerRuleController(TwoLayerRuleParameters(
            start=tuple(candidate["start"]), stop=tuple(candidate["stop"]),
            pulse_mm=float(candidate["pulse_mm"]),
            minimum_on_steps=int(candidate["minimum_on_steps"]),
            minimum_off_steps=int(candidate["minimum_off_steps"]),
        ))
    interface = protocol["controller_interface"]
    return TwoLayerMPC(model, TwoLayerMPCParameters(
        prediction_horizon_steps=int(protocol["time"]["prediction_horizon_steps"]),
        control_horizon_steps=int(protocol["time"]["control_horizon_steps"]),
        action_candidates_mm=tuple(interface["action_candidates_mm"]),
        maximum_step_command_mm=float(interface["maximum_step_command_mm"]),
        nominal_command_to_delivered_efficiency=float(
            interface["nominal_command_to_delivered_efficiency"]
        ),
        target_lower_m3_m3=tuple(interface["target_lower_m3_m3"]),
        target_upper_m3_m3=tuple(interface["target_upper_m3_m3"]),
        safety_lower_m3_m3=tuple(interface["safety_lower_m3_m3"]),
        safety_upper_m3_m3=tuple(interface["safety_upper_m3_m3"]),
        deficit_weights=tuple(interface["deficit_weights"]),
        excess_weights=tuple(interface["excess_weights"]),
        hard_safety_penalty=float(interface["hard_safety_penalty"]),
        dry_weight=float(candidate["dry_weight"]), wet_weight=float(candidate["wet_weight"]),
        water_weight=float(candidate["water_weight"]),
        movement_weight=float(candidate["movement_weight"]),
        terminal_weight=float(candidate["terminal_weight"]),
    ))


def run_control_scenario(
    row: pd.Series, kind: str, candidate: dict, protocol: dict, model: TwoLayerGreyBox
) -> dict:
    plant = TwoLayerRootZonePlant(
        _plant_parameters(row, protocol),
        float(row["initial_theta_shallow"]), float(row["initial_theta_deep"]),
        int(row["scenario_seed"]),
    )
    controller = _controller(kind, candidate, protocol, model)
    measured = plant.measure()
    steps = int(protocol["time"]["evaluation_steps"])
    horizon = int(protocol["time"]["prediction_horizon_steps"])
    phase = int(row["weather_phase_index"])
    interface = protocol["controller_interface"]
    lower = np.asarray(interface["target_lower_m3_m3"])
    upper = np.asarray(interface["target_upper_m3_m3"])
    safety_lower = np.asarray(interface["safety_lower_m3_m3"])
    safety_upper = np.asarray(interface["safety_upper_m3_m3"])
    deficit_weights = np.asarray(interface["deficit_weights"])
    excess_weights = np.asarray(interface["excess_weights"])
    deficit_by_layer = np.zeros(2)
    excess_by_layer = np.zeros(2)
    safety_by_layer = np.zeros(2, dtype=int)
    commands: list[float] = []
    delivered_total = 0.0
    drainage_total = 0.0
    maximum_balance_residual = 0.0
    for step in range(steps):
        forecast = [_weather(step + offset, phase) for offset in range(horizon)]
        solar_forecast = np.asarray([
            item[0] * float(row["solar_multiplier"])
            * (1.0 + float(row["forecast_solar_bias_fraction"]))
            for item in forecast
        ])
        vpd_forecast = np.asarray([
            max(
                0.05,
                item[1] + float(row["vpd_additive_kpa"])
                + float(row["forecast_vpd_bias_kpa"]),
            )
            for item in forecast
        ])
        command = controller.act(measured, solar_forecast, vpd_forecast)
        solar, vpd, _, _ = _weather(step, phase)
        solar *= float(row["solar_multiplier"])
        vpd = max(0.05, vpd + float(row["vpd_additive_kpa"]))
        outcome = plant.step(command, solar, vpd)
        measured = (outcome.theta_shallow_measured, outcome.theta_deep_measured)
        state = np.asarray([outcome.theta_shallow_true, outcome.theta_deep_true])
        deficit_by_layer += np.maximum(lower - state, 0.0)
        excess_by_layer += np.maximum(state - upper, 0.0)
        safety_by_layer += ((state < safety_lower) | (state > safety_upper)).astype(int)
        commands.append(command)
        delivered_total += outcome.irrigation_delivered_mm
        drainage_total += outcome.deep_drainage_mm
        maximum_balance_residual = max(
            maximum_balance_residual, abs(outcome.water_balance_residual_mm)
        )
    actions = np.asarray(commands)
    return {
        "scenario_id": str(row["scenario_id"]),
        "split": str(row["split"]),
        "controller": kind,
        "candidate_id": candidate["candidate_id"],
        "shallow_deficit_integral": float(deficit_by_layer[0]),
        "deep_deficit_integral": float(deficit_by_layer[1]),
        "weighted_deficit_integral": float(np.sum(deficit_weights * deficit_by_layer)),
        "weighted_excess_integral": float(np.sum(excess_weights * excess_by_layer)),
        "irrigation_command_total_mm": float(np.sum(actions)),
        "irrigation_delivered_total_mm": float(delivered_total),
        "deep_drainage_total_mm": float(drainage_total),
        "action_change_count": int(np.count_nonzero(np.diff(actions) != 0)),
        "shallow_safety_violation_steps": int(safety_by_layer[0]),
        "deep_safety_violation_steps": int(safety_by_layer[1]),
        "safety_violation_steps": int(np.sum(safety_by_layer)),
        "max_abs_balance_residual_mm": float(maximum_balance_residual),
    }


def _summarize_candidates(raw: pd.DataFrame, protocol: dict) -> pd.DataFrame:
    rows = []
    criteria = protocol["development_design"]["feasibility"]
    for (kind, candidate_id), group in raw.groupby(["controller", "candidate_id"], sort=True):
        safety_scenarios = int((group["safety_violation_steps"] > 0).sum())
        median_deficit = float(group["weighted_deficit_integral"].median())
        p90_deficit = float(group["weighted_deficit_integral"].quantile(0.90))
        row = {
            "controller": kind,
            "candidate_id": candidate_id,
            "scenario_count": int(len(group)),
            "safety_violation_scenarios": safety_scenarios,
            "median_weighted_deficit_integral": median_deficit,
            "p90_weighted_deficit_integral": p90_deficit,
            "median_irrigation_command_total_mm": float(
                group["irrigation_command_total_mm"].median()
            ),
            "median_action_change_count": float(group["action_change_count"].median()),
        }
        row["feasible"] = bool(
            safety_scenarios <= int(criteria["maximum_safety_violation_scenarios"])
            and median_deficit <= float(criteria["maximum_median_weighted_deficit_integral"])
            and p90_deficit <= float(criteria["maximum_p90_weighted_deficit_integral"])
        )
        rows.append(row)
    return pd.DataFrame(rows)


def _select(summary: pd.DataFrame, kind: str) -> dict | None:
    group = summary[(summary["controller"] == kind) & summary["feasible"]].copy()
    if group.empty:
        return None
    group = group.sort_values([
        "median_irrigation_command_total_mm",
        "median_weighted_deficit_integral",
        "median_action_change_count",
        "candidate_id",
    ])
    return group.iloc[0].to_dict()


def run_two_layer_control_development(project_root: Path) -> tuple[Path, ...]:
    protocol_path = project_root / "configs/two_layer_control_development_v1.yaml"
    model_path = project_root / "data/processed/two_layer_bridge_identification_v1.json"
    protocol = load_protocol(protocol_path)
    model_result = json.loads(model_path.read_text(encoding="utf-8"))
    if model_result.get("status") != "passed":
        raise ValueError("Two-layer model did not pass frozen validation")
    model = _load_fitted_model(model_result)
    scenarios, scenario_audit = build_two_layer_control_scenarios(protocol)
    development = scenarios[scenarios["split"] == "development"].copy()
    rows = []
    for kind in ("rule", "mpc"):
        candidates = protocol["development_candidates"][kind]
        limit = int(protocol["development_design"][f"{kind}_candidate_limit"])
        if len(candidates) > limit:
            raise ValueError(f"{kind} candidate count exceeds frozen limit")
        for candidate in candidates:
            for _, scenario in development.iterrows():
                rows.append(run_control_scenario(scenario, kind, candidate, protocol, model))
    raw = pd.DataFrame(rows)
    summary = _summarize_candidates(raw, protocol)
    selected = {kind: _select(summary, kind) for kind in ("rule", "mpc")}
    status = "passed" if scenario_audit["status"] == "passed" and all(selected.values()) else "failed"

    data_dir = project_root / "data/processed"
    table_dir = project_root / "outputs/tables"
    data_dir.mkdir(parents=True, exist_ok=True)
    table_dir.mkdir(parents=True, exist_ok=True)
    scenario_path = data_dir / "two_layer_control_scenarios_v1.csv"
    raw_path = table_dir / "two_layer_control_development_runs_v1.csv"
    summary_path = table_dir / "two_layer_control_development_candidates_v1.csv"
    result_path = data_dir / "two_layer_control_development_v1.json"
    frozen_path = project_root / "configs/two_layer_frozen_controller_v1.yaml"
    scenarios.to_csv(scenario_path, index=False, float_format="%.10f")
    raw.to_csv(raw_path, index=False)
    summary.to_csv(summary_path, index=False)
    result = {
        "name": "two_layer_control_development_v1",
        "status": status,
        "code_commit": _git_commit(project_root),
        "protocol_config_sha256": _sha256(protocol_path),
        "model_result_sha256": _sha256(model_path),
        "scenario_manifest_sha256": _sha256(scenario_path),
        "development_scenarios_used": int(len(development)),
        "locked_evaluation_scenarios_used": 0,
        "candidate_counts": {
            kind: len(protocol["development_candidates"][kind]) for kind in ("rule", "mpc")
        },
        "feasible_candidate_counts": {
            kind: int(summary[(summary["controller"] == kind) & summary["feasible"]].shape[0])
            for kind in ("rule", "mpc")
        },
        "selected": selected,
        "scenario_checks": scenario_audit["checks"],
        "maximum_absolute_water_balance_residual_mm": float(
            raw["max_abs_balance_residual_mm"].max()
        ),
        "evidence_boundary": "independent_synthetic_two_layer_development_selection_only",
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    frozen = {
        "two_layer_frozen_controller": {
            "status": "frozen" if status == "passed" else "not_frozen",
            "selected_on_split": "development",
            "locked_evaluation_accessed": False,
            "protocol_config_sha256": result["protocol_config_sha256"],
            "model_result": str(model_path.relative_to(project_root)),
            "model_result_sha256": result["model_result_sha256"],
            "scenario_manifest": str(scenario_path.relative_to(project_root)),
            "scenario_manifest_sha256": result["scenario_manifest_sha256"],
            "rule": next((c for c in protocol["development_candidates"]["rule"]
                          if selected["rule"] and c["candidate_id"] == selected["rule"]["candidate_id"]), None),
            "mpc": next((c for c in protocol["development_candidates"]["mpc"]
                         if selected["mpc"] and c["candidate_id"] == selected["mpc"]["candidate_id"]), None),
        }
    }
    frozen_path.write_text(yaml.safe_dump(frozen, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return scenario_path, raw_path, summary_path, result_path, frozen_path
