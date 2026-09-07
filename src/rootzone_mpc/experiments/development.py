from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rootzone_mpc.controllers import (
    InternalModel,
    RuleController,
    RuleParameters,
    ZoneMPC,
    ZoneMPCParameters,
)
from rootzone_mpc.models import PlantParameters, RootZonePlant


def _daily_shape() -> np.ndarray:
    hour = np.arange(24)
    daylight = np.maximum(np.sin(np.pi * (hour - 6) / 12), 0.0)
    return daylight / daylight.sum()


def build_et_series(row: pd.Series, total_hours: int) -> np.ndarray:
    rng = np.random.default_rng(int(row["scenario_seed"]) + 101)
    days = int(np.ceil(total_hours / 24))
    daily_total = 4.2 * float(row["et_multiplier"])
    totals = np.full(days, daily_total)
    profile = row["et_profile"]
    if profile == "hot_spell":
        start = max(1, days // 3)
        totals[start : min(start + 4, days)] *= 1.35
    elif profile == "variable_cloud":
        totals *= rng.uniform(0.70, 1.25, size=days)
    elif profile != "typical":
        raise ValueError(f"unknown ET profile: {profile}")
    return np.concatenate([total * _daily_shape() for total in totals])[:total_hours]


def _plant_from_row(row: pd.Series) -> RootZonePlant:
    parameters = PlantParameters(
        root_depth_mm=float(row["root_depth_mm"]),
        field_capacity=float(row["field_capacity"]),
        wilting_point=float(row["wilting_point"]),
        saturation=float(row["saturation"]),
        irrigation_efficiency=float(row["irrigation_efficiency"]),
        drainage_coefficient=float(row["drainage_coefficient"]),
        et_stress_start=float(row["et_stress_start"]),
        process_noise_std=float(row["process_noise_std"]),
        sensor_noise_std=float(row["sensor_noise_std"]),
    )
    return RootZonePlant(parameters, float(row["initial_theta"]), int(row["scenario_seed"]))


def _controller(kind: str, parameters: dict, cfg: dict):
    if kind == "rule":
        return RuleController(RuleParameters(**parameters))
    internal_model = InternalModel(**cfg["internal_model"])
    return ZoneMPC(internal_model, ZoneMPCParameters(**parameters))


def run_development_scenario(
    row: pd.Series, kind: str, parameters: dict, cfg: dict
) -> dict:
    if row["split"] != cfg["tuning"]["development_split"]:
        raise ValueError("Tuning may only use development scenarios")
    plant = _plant_from_row(row)
    controller = _controller(kind, parameters, cfg)
    hours = int(row["horizon_hours"])
    horizon = int(cfg["mpc_fixed"]["prediction_horizon"])
    actual_et = build_et_series(row, hours + horizon)
    forecast_rng = np.random.default_rng(int(row["scenario_seed"]) + 303)
    forecast_noise = forecast_rng.normal(
        0.0, float(row["forecast_noise_std_fraction"]), size=(hours, horizon)
    )
    measured = plant.measure()
    theta = []
    actions = []
    drainage = []
    for step in range(hours):
        forecast = actual_et[step : step + horizon]
        forecast = np.maximum(
            forecast
            * (1.0 + float(row["forecast_bias_fraction"]))
            * (1.0 + forecast_noise[step]),
            0.0,
        )
        action = controller.act(measured, forecast.tolist())
        result = plant.step(action, float(actual_et[step]))
        measured = result.theta_measured
        theta.append(result.theta_true)
        actions.append(action)
        drainage.append(result.drainage_mm)

    theta_array = np.asarray(theta)
    action_array = np.asarray(actions)
    zone = cfg["tuning"]["zone"]
    deficit = np.maximum(float(zone["lower"]) - theta_array, 0.0)
    excess = np.maximum(theta_array - float(zone["upper"]), 0.0)
    safety = (theta_array < float(zone["safety_lower"])) | (
        theta_array > float(zone["safety_upper"])
    )
    return {
        "scenario_id": row["scenario_id"],
        "below_zone_fraction": float(np.mean(deficit > 0.0)),
        "above_zone_fraction": float(np.mean(excess > 0.0)),
        "deficit_integral": float(deficit.sum()),
        "excess_integral": float(excess.sum()),
        "irrigation_command_total_mm": float(action_array.sum()),
        "drainage_total_mm": float(np.sum(drainage)),
        "action_change_count": int(np.count_nonzero(np.diff(action_array) != 0.0)),
        "safety_violation": bool(np.any(safety)),
    }


def _candidate_parameters(kind: str, cfg: dict) -> list[tuple[str, dict]]:
    if kind == "rule":
        grid = cfg["rule_grid"]
        fixed = cfg["rule_fixed"]
    else:
        grid = cfg["mpc_grid"]
        fixed = {
            **cfg["mpc_fixed"],
            **cfg["tuning"]["zone"],
        }
        fixed["irrigation_candidates_mm"] = tuple(fixed["irrigation_candidates_mm"])
    names = list(grid)
    candidates = []
    for index, values in enumerate(itertools.product(*(grid[name] for name in names)), start=1):
        parameters = {**fixed, **dict(zip(names, values))}
        candidates.append((f"{kind.upper()}{index:03d}", parameters))
    return candidates


def _summarize_candidate(candidate_id: str, results: pd.DataFrame, cfg: dict) -> dict:
    constraints = cfg["tuning"]["selection_constraints"]
    median_below = float(results["below_zone_fraction"].median())
    p90_below = float(results["below_zone_fraction"].quantile(0.90))
    safety_count = int(results["safety_violation"].sum())
    feasible = (
        median_below <= float(constraints["median_below_zone_fraction_max"])
        and p90_below <= float(constraints["p90_below_zone_fraction_max"])
        and safety_count <= int(constraints["safety_violation_scenarios_max"])
    )
    return {
        "candidate_id": candidate_id,
        "feasible": feasible,
        "median_below_zone_fraction": median_below,
        "p90_below_zone_fraction": p90_below,
        "median_deficit_integral": float(results["deficit_integral"].median()),
        "p90_deficit_integral": float(results["deficit_integral"].quantile(0.90)),
        "median_irrigation_command_total_mm": float(
            results["irrigation_command_total_mm"].median()
        ),
        "median_action_change_count": float(results["action_change_count"].median()),
        "safety_violation_scenarios": safety_count,
    }


def _select(summary: pd.DataFrame) -> pd.Series:
    feasible = summary[summary["feasible"]].copy()
    if feasible.empty:
        raise RuntimeError("No candidate satisfies the predefined development constraints")
    return feasible.sort_values(
        [
            "median_irrigation_command_total_mm",
            "median_deficit_integral",
            "median_action_change_count",
            "candidate_id",
        ]
    ).iloc[0]


def run_controller_tuning(project_root: Path) -> tuple[Path, ...]:
    tuning_path = project_root / "configs" / "tuning_design_v1.yaml"
    scenario_path = project_root / "data" / "processed" / "scenario_manifest_v1.csv"
    cfg = yaml.safe_load(tuning_path.read_text(encoding="utf-8"))
    scenarios = pd.read_csv(scenario_path)
    development = scenarios[scenarios["split"] == cfg["tuning"]["development_split"]].copy()
    if len(development) != 48:
        raise ValueError("Expected exactly 48 development scenarios")

    table_dir = project_root / "outputs" / "tables"
    audit_dir = project_root / "data" / "processed"
    table_dir.mkdir(parents=True, exist_ok=True)
    selected: dict[str, dict] = {}
    output_paths: list[Path] = []
    for kind in ("rule", "mpc"):
        summaries = []
        parameter_lookup = {}
        for candidate_id, parameters in _candidate_parameters(kind, cfg):
            parameter_lookup[candidate_id] = parameters
            results = pd.DataFrame(
                run_development_scenario(row, kind, parameters, cfg)
                for _, row in development.iterrows()
            )
            summaries.append(_summarize_candidate(candidate_id, results, cfg))
        summary_frame = pd.DataFrame(summaries)
        selected_row = _select(summary_frame)
        selected_id = str(selected_row["candidate_id"])
        selected[kind] = {
            "candidate_id": selected_id,
            "parameters": parameter_lookup[selected_id],
            "development_metrics": selected_row.to_dict(),
        }
        output_path = table_dir / f"tuning_{kind}_candidates.csv"
        summary_frame.to_csv(output_path, index=False)
        output_paths.append(output_path)

    frozen_path = project_root / "configs" / "frozen_controller_v1.yaml"
    frozen_payload = {
        "freeze_name": "frozen_controller_v1",
        "selected_on_split": "development",
        "locked_evaluation_accessed": False,
        "zone": cfg["tuning"]["zone"],
        "internal_model": cfg["internal_model"],
        "rule": selected["rule"],
        "mpc": selected["mpc"],
    }
    frozen_path.write_text(yaml.safe_dump(frozen_payload, sort_keys=False), encoding="utf-8")

    audit_path = audit_dir / "controller_selection_v1.json"
    audit = {
        "tuning_name": cfg["tuning"]["name"],
        "development_scenarios_used": int(len(development)),
        "locked_evaluation_scenarios_used": 0,
        "rule_candidate_count": len(_candidate_parameters("rule", cfg)),
        "mpc_candidate_count": len(_candidate_parameters("mpc", cfg)),
        "selected": selected,
        "tuning_config_sha256": hashlib.sha256(tuning_path.read_bytes()).hexdigest(),
        "scenario_manifest_sha256": hashlib.sha256(scenario_path.read_bytes()).hexdigest(),
    }
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return (*output_paths, frozen_path, audit_path)
