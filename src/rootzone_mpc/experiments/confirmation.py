from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rootzone_mpc.experiments.development import run_scenario


MAIN_METRICS = ("deficit_integral", "irrigation_command_total_mm")
PAIR_METRICS = (
    "below_zone_fraction",
    "above_zone_fraction",
    "deficit_integral",
    "excess_integral",
    "irrigation_command_total_mm",
    "drainage_total_mm",
    "action_change_count",
    "safety_violation_steps",
)

SCENARIO_METADATA = (
    "et_profile",
    "et_multiplier",
    "forecast_bias_fraction",
    "root_depth_mm",
    "field_capacity",
    "irrigation_efficiency",
    "drainage_coefficient",
)


def _bootstrap_median_interval(
    values: np.ndarray, resamples: int, seed: int
) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    n = len(values)
    medians = np.empty(resamples)
    batch_size = 1000
    for start in range(0, resamples, batch_size):
        stop = min(start + batch_size, resamples)
        indices = rng.integers(0, n, size=(stop - start, n))
        medians[start:stop] = np.median(values[indices], axis=1)
    lower, upper = np.quantile(medians, [0.025, 0.975])
    return float(lower), float(upper)


def _paired_results(raw: pd.DataFrame) -> pd.DataFrame:
    metadata = raw[raw["controller"] == "rule"][
        ["scenario_id", *SCENARIO_METADATA]
    ].set_index("scenario_id")
    pairs = metadata.copy()
    for metric in PAIR_METRICS:
        wide = raw.pivot(index="scenario_id", columns="controller", values=metric)
        pairs[f"delta_{metric}"] = wide["mpc"] - wide["rule"]
        pairs[f"rule_{metric}"] = wide["rule"]
        pairs[f"mpc_{metric}"] = wide["mpc"]
    return pairs.reset_index()


def _classify(pairs: pd.DataFrame, config: dict, intervals: dict) -> tuple[str, dict]:
    tie = config["confirmation"]["tie_tolerance"]
    delta_deficit = pairs["delta_deficit_integral"]
    delta_water = pairs["delta_irrigation_command_total_mm"]
    median_deficit = float(delta_deficit.median())
    median_water = float(delta_water.median())
    joint_nonworse = (
        (delta_deficit <= float(tie["deficit_integral"]))
        & (delta_water <= float(tie["irrigation_command_total_mm"]))
    )
    any_adverse = (
        (delta_deficit > float(tie["deficit_integral"]))
        | (delta_water > float(tie["irrigation_command_total_mm"]))
    )
    joint_ratio = float(joint_nonworse.mean())
    adverse_ratio = float(any_adverse.mean())
    rule_safety = int((pairs["rule_safety_violation_steps"] > 0).sum())
    mpc_safety = int((pairs["mpc_safety_violation_steps"] > 0).sum())
    no_extra_safety = mpc_safety <= rule_safety
    deficit_upper = float(intervals["deficit_integral"]["ci95_upper"])
    water_upper = float(intervals["irrigation_command_total_mm"]["ci95_upper"])

    if (
        median_deficit <= 0.0
        and median_water <= 0.0
        and (deficit_upper < 0.0 or water_upper < 0.0)
        and no_extra_safety
        and joint_ratio >= float(config["confirmation"]["joint_nonworse_ratio_min"])
        and adverse_ratio <= float(config["confirmation"]["any_endpoint_adverse_ratio_max"])
    ):
        label = "JOINT_VALUE_SUPPORTED"
    elif water_upper < 0.0 and median_deficit > 0.0 and no_extra_safety:
        label = "WATER_SAVING_WITH_DEFICIT_TRADEOFF"
    elif deficit_upper < 0.0 and median_water > 0.0 and no_extra_safety:
        label = "DEFICIT_REDUCTION_WITH_WATER_COST"
    else:
        label = "OVERALL_VALUE_NOT_SUPPORTED"
    diagnostics = {
        "joint_nonworse_ratio": joint_ratio,
        "any_endpoint_adverse_ratio": adverse_ratio,
        "rule_safety_violation_scenarios": rule_safety,
        "mpc_safety_violation_scenarios": mpc_safety,
        "no_extra_safety_violation": no_extra_safety,
    }
    return label, diagnostics


def _subgroup_results(pairs: pd.DataFrame) -> pd.DataFrame:
    work = pairs.copy()
    work["forecast_bias_group"] = pd.cut(
        work["forecast_bias_fraction"],
        bins=[-np.inf, -0.067, 0.067, np.inf],
        labels=["under_prediction", "near_unbiased", "over_prediction"],
    )
    for source, target in (
        ("root_depth_mm", "root_depth_group"),
        ("irrigation_efficiency", "irrigation_efficiency_group"),
        ("drainage_coefficient", "drainage_group"),
    ):
        work[target] = pd.qcut(work[source], 3, labels=["low", "middle", "high"])

    rows = []
    factors = [
        "et_profile",
        "forecast_bias_group",
        "root_depth_group",
        "irrigation_efficiency_group",
        "drainage_group",
    ]
    for factor in factors:
        for level, group in work.groupby(factor, observed=True):
            rows.append(
                {
                    "factor": factor,
                    "level": str(level),
                    "scenario_count": int(len(group)),
                    "median_delta_deficit_integral": float(
                        group["delta_deficit_integral"].median()
                    ),
                    "p90_delta_deficit_integral": float(
                        group["delta_deficit_integral"].quantile(0.90)
                    ),
                    "median_delta_irrigation_mm": float(
                        group["delta_irrigation_command_total_mm"].median()
                    ),
                    "p90_delta_irrigation_mm": float(
                        group["delta_irrigation_command_total_mm"].quantile(0.90)
                    ),
                }
            )
    return pd.DataFrame(rows)


def run_baseline_confirmation(project_root: Path) -> tuple[Path, ...]:
    confirm_path = project_root / "configs" / "confirmatory_design_v1.yaml"
    tuning_path = project_root / "configs" / "tuning_design_v1.yaml"
    frozen_path = project_root / "configs" / "frozen_controller_v1.yaml"
    scenario_path = project_root / "data" / "processed" / "scenario_manifest_v1.csv"
    confirmation = yaml.safe_load(confirm_path.read_text(encoding="utf-8"))
    tuning = yaml.safe_load(tuning_path.read_text(encoding="utf-8"))
    frozen = yaml.safe_load(frozen_path.read_text(encoding="utf-8"))
    scenarios = pd.read_csv(scenario_path)
    locked = scenarios[scenarios["split"] == confirmation["confirmation"]["split"]].copy()
    expected = int(confirmation["confirmation"]["expected_scenario_count"])
    if len(locked) != expected:
        raise ValueError(f"Expected {expected} locked scenarios, received {len(locked)}")
    if frozen["locked_evaluation_accessed"]:
        raise ValueError("Frozen controller metadata indicates prior locked evaluation access")

    rows = []
    for _, scenario in locked.iterrows():
        for kind in ("rule", "mpc"):
            result = run_scenario(
                scenario,
                kind,
                frozen[kind]["parameters"],
                tuning,
                allowed_split=confirmation["confirmation"]["split"],
            )
            result["controller"] = kind
            for metadata_name in SCENARIO_METADATA:
                result[metadata_name] = scenario[metadata_name]
            rows.append(result)
    raw = pd.DataFrame(rows)
    pairs = _paired_results(raw)

    resamples = int(confirmation["confirmation"]["bootstrap_resamples"])
    bootstrap_seed = int(confirmation["confirmation"]["bootstrap_seed"])
    aggregate = {}
    intervals = {}
    for index, metric in enumerate(MAIN_METRICS):
        values = pairs[f"delta_{metric}"].to_numpy(dtype=float)
        lower, upper = _bootstrap_median_interval(values, resamples, bootstrap_seed + index)
        intervals[metric] = {"ci95_lower": lower, "ci95_upper": upper}
        aggregate[metric] = {
            "paired_median": float(np.median(values)),
            "paired_p10": float(np.quantile(values, 0.10)),
            "paired_p90": float(np.quantile(values, 0.90)),
            "ci95_lower": lower,
            "ci95_upper": upper,
            "improved_ratio": float(np.mean(values < 0.0)),
            "equal_ratio": float(np.mean(values == 0.0)),
            "adverse_ratio": float(np.mean(values > 0.0)),
        }
    classification, diagnostics = _classify(pairs, confirmation, intervals)

    subgroup = _subgroup_results(pairs)

    run_dir = project_root / "outputs" / "runs"
    table_dir = project_root / "outputs" / "tables"
    run_dir.mkdir(parents=True, exist_ok=True)
    table_dir.mkdir(parents=True, exist_ok=True)
    raw_path = run_dir / "baseline_confirmation_metrics.csv"
    pairs_path = table_dir / "baseline_confirmation_pairs.csv"
    subgroup_path = table_dir / "baseline_confirmation_subgroups.csv"
    timing_path = table_dir / "baseline_confirmation_timing.csv"
    result_path = project_root / "data" / "processed" / "baseline_confirmation_v1.json"
    raw.to_csv(raw_path, index=False)
    pairs.to_csv(pairs_path, index=False)
    subgroup.to_csv(subgroup_path, index=False)
    raw.groupby("controller").agg(
        scenario_count=("scenario_id", "count"),
        median_scenario_p90_ms=("compute_ms_p90", "median"),
        maximum_scenario_p99_ms=("compute_ms_p99", "max"),
    ).reset_index().to_csv(timing_path, index=False)
    result = {
        "confirmation_name": confirmation["confirmation"]["name"],
        "classification": classification,
        "scenario_count": expected,
        "method_runs": int(len(raw)),
        "aggregate": aggregate,
        "diagnostics": diagnostics,
        "max_abs_balance_residual_mm": float(raw["max_abs_balance_residual_mm"].max()),
        "bootstrap_resamples": resamples,
        "evidence_boundary": confirmation["confirmation"]["evidence_boundary"],
        "hashes": {
            "confirmation_config": hashlib.sha256(confirm_path.read_bytes()).hexdigest(),
            "frozen_controller": hashlib.sha256(frozen_path.read_bytes()).hexdigest(),
            "scenario_manifest": hashlib.sha256(scenario_path.read_bytes()).hexdigest(),
            "paired_results": hashlib.sha256(pairs_path.read_bytes()).hexdigest(),
        },
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return raw_path, pairs_path, subgroup_path, timing_path, result_path
