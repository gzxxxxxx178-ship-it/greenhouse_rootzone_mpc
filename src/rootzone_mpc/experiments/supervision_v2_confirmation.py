from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rootzone_mpc.experiments.confirmation import _bootstrap_median_interval
from rootzone_mpc.experiments.supervision_confirmation import _pair, _run_method


def _classify(metrics: pd.DataFrame, v2_base: pd.DataFrame, v2_v1: pd.DataFrame, cfg: dict):
    confirmation = cfg["confirmation"]
    gate = confirmation["success_gate"]
    tolerance = float(confirmation["tie_tolerance_event_deficit"])
    by_type_v1 = v2_v1.groupby("anomaly_type")["delta_event_deficit_integral"].median()
    by_type_base = v2_base.groupby("anomaly_type")["delta_event_deficit_integral"].median()
    targets = confirmation["target_v1_failure_types"]
    improved_targets = int((by_type_v1.loc[targets] < -tolerance).sum())
    adverse_base = int((by_type_base.drop(index="normal") > tolerance).sum())
    v2 = metrics[metrics.method == "trustworthy_supervision_v2"]
    base = metrics[metrics.method == "base_mpc"]
    v1 = metrics[metrics.method == "trustworthy_supervision"]
    normal_non_mpc = float(v2.loc[v2.anomaly_type == "normal", "non_mpc_fraction"].median())
    relative_irrigation = (
        v2.set_index("anomaly_scenario_id").irrigation_command_total_mm
        - base.set_index("anomaly_scenario_id").irrigation_command_total_mm
    ) / base.set_index("anomaly_scenario_id").irrigation_command_total_mm
    median_relative_irrigation = float(relative_irrigation.median())
    safety = {
        name: int((frame.safety_violation_steps > 0).sum())
        for name, frame in {"v2": v2, "v1": v1, "base": base}.items()
    }
    no_extra_safety = safety["v2"] <= min(safety["v1"], safety["base"])
    passed = (
        improved_targets >= int(gate["minimum_improved_target_types_vs_v1"])
        and adverse_base <= int(gate["maximum_adverse_anomaly_types_vs_base"])
        and normal_non_mpc <= float(gate["normal_non_mpc_fraction_max"])
        and median_relative_irrigation
        <= float(gate["median_relative_irrigation_increase_vs_base_max"])
        and (no_extra_safety or not gate["require_no_extra_safety_violations"])
    )
    if passed:
        label = "V2_INDEPENDENT_CONFIRMATION_SUPPORTED"
    elif improved_targets > 0 and no_extra_safety:
        label = "V2_PARTIAL_VALUE_WITH_BOUNDARIES"
    else:
        label = "V2_VALUE_NOT_SUPPORTED"
    return label, {
        "improved_target_types_vs_v1": improved_targets,
        "adverse_anomaly_types_vs_base": adverse_base,
        "normal_non_mpc_fraction_median": normal_non_mpc,
        "median_relative_irrigation_increase_vs_base": median_relative_irrigation,
        "safety_violation_scenarios": safety,
        "no_extra_safety_violations": no_extra_safety,
        "success_gate_passed": passed,
    }


def run_supervision_v2_confirmation(project_root: Path) -> tuple[Path, ...]:
    paths = {
        "confirmation": project_root / "configs/supervision_v2_confirmation.yaml",
        "anomaly": project_root / "configs/anomaly_design_v1.yaml",
        "tuning": project_root / "configs/tuning_design_v1.yaml",
        "controller": project_root / "configs/frozen_controller_v1.yaml",
        "supervision_v1": project_root / "configs/frozen_supervision_v1.yaml",
        "supervision_v2": project_root / "configs/frozen_supervision_v2.yaml",
        "scenarios": project_root / "data/processed/supervision_v2_scenario_manifest.csv",
        "anomalies": project_root / "data/processed/supervision_v2_anomaly_manifest.csv",
        "manifest_audit": project_root / "data/processed/supervision_v2_manifest.audit.json",
    }
    loaded = {
        key: yaml.safe_load(path.read_text(encoding="utf-8"))
        for key, path in paths.items() if path.suffix in {".yaml", ".yml"}
    }
    audit = json.loads(paths["manifest_audit"].read_text(encoding="utf-8"))
    if audit["locked_evaluation_scenarios_used"] != 0:
        raise ValueError("Locked V2 scenarios were accessed before confirmation")
    if int(loaded["supervision_v2"]["locked_evaluation_scenarios_used"]) != 0:
        raise ValueError("Frozen V2 configuration indicates locked evaluation access")
    scenarios = pd.read_csv(paths["scenarios"]).set_index("scenario_id")
    anomalies = pd.read_csv(paths["anomalies"])
    locked = anomalies[anomalies.split == loaded["confirmation"]["confirmation"]["split"]]
    expected = int(loaded["confirmation"]["confirmation"]["expected_scenario_count"])
    if len(locked) != expected:
        raise ValueError("Locked V2 scenario count differs from contract")
    rows, traces = [], []
    for _, anomaly in locked.iterrows():
        scenario = scenarios.loc[anomaly.base_scenario_id].copy()
        scenario["scenario_id"] = anomaly.base_scenario_id
        for method in loaded["confirmation"]["confirmation"]["methods"]:
            v2 = loaded["supervision_v2"] if method == "trustworthy_supervision_v2" else None
            metric, trace = _run_method(
                scenario, anomaly, method, loaded["tuning"], loaded["controller"],
                loaded["supervision_v1"], loaded["anomaly"], loaded["confirmation"], v2,
            )
            rows.append(metric)
            traces.append(trace)
    metrics = pd.DataFrame(rows)
    trace_frame = pd.concat(traces, ignore_index=True)
    v2_base = _pair(metrics, "trustworthy_supervision_v2", "base_mpc")
    v2_v1 = _pair(metrics, "trustworthy_supervision_v2", "trustworthy_supervision")
    label, diagnostics = _classify(metrics, v2_base, v2_v1, loaded["confirmation"])
    subgroup = v2_base.groupby("anomaly_type").agg(
        scenario_count=("anomaly_scenario_id", "count"),
        median_delta_event_deficit_vs_base=("delta_event_deficit_integral", "median"),
        p90_delta_event_deficit_vs_base=("delta_event_deficit_integral", lambda x: x.quantile(0.9)),
        median_delta_irrigation_mm_vs_base=("delta_irrigation_command_total_mm", "median"),
    ).reset_index()
    v1_delta = v2_v1.groupby("anomaly_type")["delta_event_deficit_integral"].median()
    subgroup["median_delta_event_deficit_vs_v1"] = subgroup.anomaly_type.map(v1_delta)
    overall = {}
    for comparison, pairs in {"v2_minus_base": v2_base, "v2_minus_v1": v2_v1}.items():
        overall[comparison] = {}
        for index, metric in enumerate(["event_deficit_integral", "deficit_integral", "irrigation_command_total_mm"]):
            values = pairs[f"delta_{metric}"].to_numpy(float)
            lower, upper = _bootstrap_median_interval(
                values, int(loaded["confirmation"]["confirmation"]["bootstrap_resamples"]),
                int(loaded["confirmation"]["confirmation"]["bootstrap_seed"]) + index,
            )
            overall[comparison][metric] = {
                "paired_median": float(np.median(values)), "ci95_lower": lower,
                "ci95_upper": upper, "improved_ratio": float(np.mean(values < 0)),
                "adverse_ratio": float(np.mean(values > 0)),
            }
    run_dir, table_dir = project_root / "outputs/runs", project_root / "outputs/tables"
    metrics_path = run_dir / "supervision_v2_confirmation_metrics.csv"
    traces_path = run_dir / "supervision_v2_confirmation_traces.csv"
    base_path = table_dir / "supervision_v2_vs_base_pairs.csv"
    v1_path = table_dir / "supervision_v2_vs_v1_pairs.csv"
    subgroup_path = table_dir / "supervision_v2_confirmation_subgroups.csv"
    metrics.to_csv(metrics_path, index=False)
    trace_frame.to_csv(traces_path, index=False)
    v2_base.to_csv(base_path, index=False)
    v2_v1.to_csv(v1_path, index=False)
    subgroup.to_csv(subgroup_path, index=False)
    result_path = project_root / "data/processed/supervision_v2_confirmation_v1.json"
    result = {
        "confirmation_name": loaded["confirmation"]["confirmation"]["name"],
        "classification": label, "scenario_count": len(locked), "method_runs": len(metrics),
        "diagnostics": diagnostics, "overall": overall,
        "evidence_boundary": loaded["confirmation"]["confirmation"]["evidence_boundary"],
        "hashes": {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in {
            "confirmation_config": paths["confirmation"], "frozen_supervision_v2": paths["supervision_v2"],
            "scenario_manifest": paths["scenarios"], "anomaly_manifest": paths["anomalies"],
            "v2_vs_base_pairs": base_path, "v2_vs_v1_pairs": v1_path,
        }.items()},
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics_path, traces_path, base_path, v1_path, subgroup_path, result_path
