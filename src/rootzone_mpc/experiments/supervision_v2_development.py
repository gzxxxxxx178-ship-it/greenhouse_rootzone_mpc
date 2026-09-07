from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rootzone_mpc.experiments.supervision_confirmation import _run_method


def _candidate_config(candidate: dict, cfg: dict) -> dict:
    fixed = cfg["fixed"]
    return {
        "supervisor": fixed["supervisor"],
        "estimator": {
            "trusted_observation_gain": candidate["trusted_observation_gain"],
            "uncertainty_growth_per_step": candidate["uncertainty_growth_per_step"],
            "uncertainty_reduction_factor": fixed["estimator"]["uncertainty_reduction_factor"],
            "maximum_uncertainty": candidate["maximum_uncertainty"],
            "valid_theta_min": fixed["estimator"]["valid_theta_min"],
            "valid_theta_max": fixed["estimator"]["valid_theta_max"],
        },
        "fallback_rule": {
            "start_threshold": candidate["start_threshold"],
            **fixed["fallback_rule"],
        },
    }


def run_supervision_v2_development(project_root: Path) -> tuple[Path, Path, Path, Path]:
    config_path = project_root / "configs/supervision_v2_development.yaml"
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    paths = {
        "anomaly": project_root / "configs/anomaly_design_v1.yaml",
        "confirm": project_root / "configs/supervision_confirmation_v1.yaml",
        "tuning": project_root / "configs/tuning_design_v1.yaml",
        "controller": project_root / "configs/frozen_controller_v1.yaml",
        "supervision": project_root / "configs/frozen_supervision_v1.yaml",
        "scenarios": project_root / "data/processed/supervision_v2_scenario_manifest.csv",
        "anomalies": project_root / "data/processed/supervision_v2_anomaly_manifest.csv",
        "manifest_audit": project_root / "data/processed/supervision_v2_manifest.audit.json",
    }
    loaded = {
        key: yaml.safe_load(path.read_text(encoding="utf-8"))
        for key, path in paths.items()
        if path.suffix in {".yaml", ".yml"}
    }
    audit = json.loads(paths["manifest_audit"].read_text(encoding="utf-8"))
    if audit["locked_evaluation_scenarios_used"] != 0:
        raise ValueError("V2 manifest audit indicates locked evaluation access")
    scenarios = pd.read_csv(paths["scenarios"]).set_index("scenario_id")
    anomalies = pd.read_csv(paths["anomalies"])
    development = anomalies[anomalies["split"] == cfg["development"]["split"]]
    if len(development) != int(cfg["development"]["expected_scenario_count"]):
        raise ValueError("V2 development scenario count differs from contract")

    rows = []
    methods = [(name, None) for name in cfg["development"]["reference_methods"]]
    methods += [
        (candidate["candidate_id"], _candidate_config(candidate, cfg))
        for candidate in cfg["candidates"]
    ]
    for _, anomaly in development.iterrows():
        scenario = scenarios.loc[anomaly["base_scenario_id"]].copy()
        scenario["scenario_id"] = anomaly["base_scenario_id"]
        for label, v2 in methods:
            method = "trustworthy_supervision_v2" if v2 is not None else label
            metric, _ = _run_method(
                scenario, anomaly, method, loaded["tuning"], loaded["controller"],
                loaded["supervision"], loaded["anomaly"], loaded["confirm"], v2,
            )
            metric["method"] = label
            rows.append(metric)
    metrics = pd.DataFrame(rows)
    tolerance = float(cfg["development"]["tie_tolerance_event_deficit"])
    target_types = set(cfg["development"]["target_prior_adverse_types"])
    summaries = []
    for candidate in cfg["candidates"]:
        label = candidate["candidate_id"]
        current = metrics[metrics["method"] == label]
        v1 = metrics[metrics["method"] == "trustworthy_supervision"]
        base = metrics[metrics["method"] == "base_mpc"]
        keyed = ["anomaly_scenario_id", "anomaly_type"]
        paired_v1 = current.merge(v1, on=keyed, suffixes=("_v2", "_v1"))
        paired_base = current.merge(base, on=keyed, suffixes=("_v2", "_base"))
        by_type_v1 = paired_v1.assign(
            delta=lambda x: x.event_deficit_integral_v2 - x.event_deficit_integral_v1
        ).groupby("anomaly_type").delta.median()
        by_type_base = paired_base.assign(
            delta=lambda x: x.event_deficit_integral_v2 - x.event_deficit_integral_base
        ).groupby("anomaly_type").delta.median()
        improved = int((by_type_v1.loc[list(target_types)] < -tolerance).sum())
        adverse = int((by_type_base.drop(index="normal") > tolerance).sum())
        normal_non_mpc = float(current.loc[current.anomaly_type == "normal", "non_mpc_fraction"].median())
        safety = int((current.safety_violation_steps > 0).sum())
        v1_safety = int((v1.safety_violation_steps > 0).sum())
        infeasible = int(
            normal_non_mpc > cfg["development"]["feasibility"]["maximum_normal_non_mpc_fraction"]
            or safety > v1_safety
        )
        summaries.append({
            **candidate, "infeasible": infeasible,
            "improved_prior_adverse_types_vs_v1": improved,
            "adverse_types_vs_base": adverse,
            "normal_non_mpc_fraction_median": normal_non_mpc,
            "safety_violation_scenarios": safety,
            "v1_safety_violation_scenarios": v1_safety,
            "median_event_deficit": float(current.event_deficit_integral.median()),
            "p90_event_deficit": float(current.event_deficit_integral.quantile(0.9)),
            "median_irrigation_mm": float(current.irrigation_command_total_mm.median()),
        })
    summary = pd.DataFrame(summaries).sort_values(
        ["infeasible", "adverse_types_vs_base", "improved_prior_adverse_types_vs_v1",
         "median_event_deficit", "median_irrigation_mm"],
        ascending=[True, True, False, True, True],
    )
    selected_id = summary.iloc[0]["candidate_id"]
    selected = next(item for item in cfg["candidates"] if item["candidate_id"] == selected_id)
    frozen = {
        "freeze_name": "frozen_supervision_v2",
        "selected_candidate_id": selected_id,
        "development_scenarios_used": int(len(development)),
        "locked_evaluation_scenarios_used": 0,
        **_candidate_config(selected, cfg),
        "hashes": {
            "development_config": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "scenario_manifest": hashlib.sha256(paths["scenarios"].read_bytes()).hexdigest(),
            "anomaly_manifest": hashlib.sha256(paths["anomalies"].read_bytes()).hexdigest(),
        },
    }
    metrics_path = project_root / "outputs/runs/supervision_v2_development_metrics.csv"
    summary_path = project_root / "outputs/tables/supervision_v2_candidate_summary.csv"
    frozen_path = project_root / "configs/frozen_supervision_v2.yaml"
    metrics.to_csv(metrics_path, index=False)
    summary.to_csv(summary_path, index=False)
    frozen_path.write_text(yaml.safe_dump(frozen, sort_keys=False), encoding="utf-8")
    result_path = project_root / "data/processed/supervision_v2_development_v1.json"
    selected_summary = json.loads(summary.iloc[[0]].to_json(orient="records"))[0]
    result = {
        "development_name": cfg["development"]["name"],
        "selected_candidate": selected_summary,
        "candidate_count": len(cfg["candidates"]),
        "scenario_count": int(len(development)),
        "method_runs": int(len(metrics)),
        "locked_evaluation_scenarios_used": 0,
        "interpretation": "development_selection_not_confirmatory_evidence",
        "hashes": {
            "metrics": hashlib.sha256(metrics_path.read_bytes()).hexdigest(),
            "candidate_summary": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
            "frozen_supervision_v2": hashlib.sha256(frozen_path.read_bytes()).hexdigest(),
        },
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics_path, summary_path, frozen_path, result_path
