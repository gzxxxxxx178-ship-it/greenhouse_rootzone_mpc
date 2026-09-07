from __future__ import annotations

import copy
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
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


def _daily_et_profile(hours: int, multiplier: float) -> np.ndarray:
    clock = np.arange(24)
    daylight = np.maximum(np.sin(np.pi * (clock - 6) / 12), 0.0)
    base_daily_total = 4.2
    daily_profile = base_daily_total * multiplier * daylight / daylight.sum()
    repeats = int(np.ceil(hours / 24))
    return np.tile(daily_profile, repeats)[:hours]


def _build_controller(name: str, cfg: dict):
    if name == "rule":
        return RuleController(RuleParameters(**cfg["rule"]))
    model = InternalModel(**cfg["internal_model"])
    mpc_cfg = dict(cfg["mpc"])
    mpc_cfg["irrigation_candidates_mm"] = tuple(mpc_cfg["irrigation_candidates_mm"])
    parameters = ZoneMPCParameters(
        lower=cfg["zone"]["lower"],
        upper=cfg["zone"]["upper"],
        safety_lower=cfg["zone"]["safety_lower"],
        safety_upper=cfg["zone"]["safety_upper"],
        **mpc_cfg,
    )
    return ZoneMPC(model, parameters)


def _run_one(cfg: dict, scenario: dict, controller_name: str, seed: int) -> pd.DataFrame:
    p = copy.deepcopy(cfg["plant_nominal"])
    p["irrigation_efficiency"] *= scenario["irrigation_efficiency_multiplier"]
    p["drainage_coefficient"] *= scenario["drainage_multiplier"]
    plant = RootZonePlant(PlantParameters(**p), cfg["experiment"]["initial_theta"], seed)
    controller = _build_controller(controller_name, cfg)
    hours = cfg["experiment"]["hours"]
    horizon = cfg["mpc"]["prediction_horizon"]
    et = _daily_et_profile(hours + horizon, scenario["et_multiplier"])
    rows = []
    measured = plant.measure()
    for k in range(hours):
        forecast = et[k : k + horizon].tolist()
        started = time.perf_counter()
        command = controller.act(measured, forecast)
        compute_ms = (time.perf_counter() - started) * 1000
        result = plant.step(command, float(et[k]))
        measured = result.theta_measured
        rows.append(
            {
                "time_h": k * cfg["experiment"]["dt_hours"],
                "scenario": scenario["name"],
                "controller": controller_name,
                "theta_true": result.theta_true,
                "theta_measured": result.theta_measured,
                "irrigation_command_mm": command,
                "irrigation_actual_mm": result.irrigation_actual_mm,
                "et_demand_mm": float(et[k]),
                "et_actual_mm": result.et_actual_mm,
                "drainage_mm": result.drainage_mm,
                "water_balance_residual_mm": result.water_balance_residual_mm,
                "compute_ms": compute_ms,
            }
        )
    return pd.DataFrame(rows)


def _summarize(frame: pd.DataFrame, cfg: dict) -> dict:
    lower = cfg["zone"]["lower"]
    upper = cfg["zone"]["upper"]
    below = np.maximum(lower - frame["theta_true"].to_numpy(), 0.0)
    above = np.maximum(frame["theta_true"].to_numpy() - upper, 0.0)
    actions = frame["irrigation_command_mm"].to_numpy()
    return {
        "scenario": frame["scenario"].iloc[0],
        "controller": frame["controller"].iloc[0],
        "below_zone_steps": int(np.count_nonzero(below > 0)),
        "above_zone_steps": int(np.count_nonzero(above > 0)),
        "deficit_integral": float(below.sum()),
        "excess_integral": float(above.sum()),
        "irrigation_command_total_mm": float(actions.sum()),
        "irrigation_actual_total_mm": float(frame["irrigation_actual_mm"].sum()),
        "drainage_total_mm": float(frame["drainage_mm"].sum()),
        "action_change_count": int(np.count_nonzero(np.diff(actions) != 0)),
        "compute_ms_p90": float(frame["compute_ms"].quantile(0.90)),
        "max_abs_balance_residual_mm": float(frame["water_balance_residual_mm"].abs().max()),
    }


def _plot(traces: pd.DataFrame, cfg: dict, output_path: Path) -> None:
    scenarios = list(traces["scenario"].drop_duplicates())
    fig, axes = plt.subplots(len(scenarios), 1, figsize=(11, 2.8 * len(scenarios)), sharex=True)
    axes = np.atleast_1d(axes)
    for ax, scenario in zip(axes, scenarios):
        subset = traces[traces["scenario"] == scenario]
        for controller, group in subset.groupby("controller"):
            ax.plot(group["time_h"], group["theta_true"], label=controller, linewidth=1.2)
        ax.axhspan(cfg["zone"]["lower"], cfg["zone"]["upper"], color="#91c788", alpha=0.22)
        ax.set_ylabel("theta")
        ax.set_title(scenario)
        ax.grid(alpha=0.25)
        ax.legend(loc="best")
    axes[-1].set_xlabel("time (h)")
    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def run_smoke_experiment(config_path: Path, project_root: Path) -> tuple[Path, ...]:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    traces = []
    summaries = []
    base_seed = int(cfg["experiment"]["seed"])
    for scenario_index, scenario in enumerate(cfg["scenarios"]):
        paired_seed = base_seed + scenario_index
        for controller_name in ("rule", "mpc"):
            frame = _run_one(cfg, scenario, controller_name, paired_seed)
            traces.append(frame)
            summaries.append(_summarize(frame, cfg))
    trace_df = pd.concat(traces, ignore_index=True)
    summary_df = pd.DataFrame(summaries)

    run_dir = project_root / "outputs" / "runs"
    table_dir = project_root / "outputs" / "tables"
    figure_dir = project_root / "outputs" / "figures"
    for directory in (run_dir, table_dir, figure_dir):
        directory.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir / "smoke_trajectories.csv"
    summary_path = table_dir / "smoke_summary.csv"
    comparison_path = table_dir / "smoke_mpc_minus_rule.csv"
    figure_path = figure_dir / "smoke_theta_comparison.png"
    manifest_path = run_dir / "smoke_manifest.json"
    trace_df.to_csv(trace_path, index=False)
    summary_df.to_csv(summary_path, index=False)
    metric_columns = [
        "below_zone_steps",
        "above_zone_steps",
        "deficit_integral",
        "excess_integral",
        "irrigation_command_total_mm",
        "irrigation_actual_total_mm",
        "drainage_total_mm",
        "action_change_count",
        "compute_ms_p90",
    ]
    wide = summary_df.pivot(index="scenario", columns="controller", values=metric_columns)
    comparison = pd.DataFrame(index=wide.index)
    for metric in metric_columns:
        comparison[f"delta_{metric}"] = wide[(metric, "mpc")] - wide[(metric, "rule")]
    comparison.reset_index().to_csv(comparison_path, index=False)
    _plot(trace_df, cfg, figure_path)
    artifacts = [trace_path, summary_path, comparison_path, figure_path]
    manifest = {
        "experiment": cfg["experiment"]["name"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": str(config_path.relative_to(project_root)),
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "artifacts": {
            str(path.relative_to(project_root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in artifacts
        },
        "evidence_boundary": "Smoke-test simulation only; not a confirmatory performance result.",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return trace_path, summary_path, comparison_path, figure_path, manifest_path
