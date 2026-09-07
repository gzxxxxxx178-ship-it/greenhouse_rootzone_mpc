from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rootzone_mpc.experiments.supervision_confirmation import _run_method


def _v3_config(candidate: dict, development: dict, frozen_v2: dict) -> dict:
    config = {
        key: value for key, value in frozen_v2.items()
        if key in {"supervisor", "estimator", "fallback_rule"}
    }
    config["estimator"] = {
        **config["estimator"],
        "recovery_observation_gain": development["fixed"]["recovery_observation_gain"],
        "recovery_maximum_innovation": development["fixed"]["recovery_maximum_innovation"],
    }
    config["recovery_probe"] = {
        "acceptance_band": candidate["acceptance_band"],
        "confirmation_steps": candidate["confirmation_steps"],
    }
    config["safety_filter"] = {
        "wet_guard_threshold": candidate["wet_guard_threshold"],
        "rolling_window_steps": development["fixed"]["rolling_window_steps"],
        "rolling_budget_mm": candidate["rolling_budget_mm"],
    }
    return config


def run_supervision_v3_development(project_root: Path) -> tuple[Path, ...]:
    config_path = project_root / "configs/supervision_v3_development.yaml"
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    paths = {
        "anomaly": project_root / "configs/anomaly_design_v1.yaml",
        "confirm": project_root / "configs/supervision_confirmation_v1.yaml",
        "tuning": project_root / "configs/tuning_design_v1.yaml",
        "controller": project_root / "configs/frozen_controller_v1.yaml",
        "v1": project_root / "configs/frozen_supervision_v1.yaml",
        "v2": project_root / "configs/frozen_supervision_v2.yaml",
        "scenarios": project_root / "data/processed/supervision_v3_scenario_manifest.csv",
        "anomalies": project_root / "data/processed/supervision_v3_anomaly_manifest.csv",
        "audit": project_root / "data/processed/supervision_v3_manifest.audit.json",
    }
    loaded = {
        key: yaml.safe_load(path.read_text(encoding="utf-8"))
        for key, path in paths.items() if path.suffix in {".yaml", ".yml"}
    }
    audit = json.loads(paths["audit"].read_text(encoding="utf-8"))
    if audit["locked_evaluation_scenarios_used"] != 0:
        raise ValueError("V3 locked evaluation scenarios were accessed before freeze")
    scenarios = pd.read_csv(paths["scenarios"]).set_index("scenario_id")
    anomalies = pd.read_csv(paths["anomalies"])
    development = anomalies[anomalies.split == cfg["development"]["split"]]
    if len(development) != int(cfg["development"]["expected_scenario_count"]):
        raise ValueError("V3 development scenario count differs from contract")
    candidate_configs = {
        item["candidate_id"]: _v3_config(item, cfg, loaded["v2"])
        for item in cfg["candidates"]
    }
    rows = []
    for _, anomaly in development.iterrows():
        scenario = scenarios.loc[anomaly.base_scenario_id].copy()
        scenario["scenario_id"] = anomaly.base_scenario_id
        methods = [
            ("base_mpc", "base_mpc", None),
            ("trustworthy_supervision", "trustworthy_supervision", None),
            ("trustworthy_supervision_v2", "trustworthy_supervision_v2", loaded["v2"]),
        ] + [(label, "trustworthy_supervision_v3", value) for label, value in candidate_configs.items()]
        for label, method, method_config in methods:
            metric, _ = _run_method(
                scenario, anomaly, method, loaded["tuning"], loaded["controller"],
                loaded["v1"], loaded["anomaly"], loaded["confirm"], method_config,
            )
            metric["method"] = label
            rows.append(metric)
    metrics = pd.DataFrame(rows)
    base = metrics[metrics.method == "base_mpc"].set_index("anomaly_scenario_id")
    v2 = metrics[metrics.method == "trustworthy_supervision_v2"].set_index("anomaly_scenario_id")
    tolerance = float(cfg["development"]["tie_tolerance_event_deficit"])
    summaries = []
    for candidate in cfg["candidates"]:
        label = candidate["candidate_id"]
        current = metrics[metrics.method == label].set_index("anomaly_scenario_id")
        delta_base = current.event_deficit_integral - base.event_deficit_integral
        delta_v2 = current.event_deficit_integral - v2.event_deficit_integral
        by_type_base = delta_base.groupby(current.anomaly_type).median()
        by_type_v2 = delta_v2.groupby(current.anomaly_type).median()
        relative_water = (
            current.irrigation_command_total_mm - base.irrigation_command_total_mm
        ) / base.irrigation_command_total_mm
        normal_non_mpc = float(current.loc[current.anomaly_type == "normal", "non_mpc_fraction"].median())
        safety = int((current.safety_violation_steps > 0).sum())
        reference_safety = min(
            int((base.safety_violation_steps > 0).sum()),
            int((v2.safety_violation_steps > 0).sum()),
        )
        water_increase = float(relative_water.median())
        infeasible = int(
            normal_non_mpc > cfg["development"]["feasibility"]["maximum_normal_non_mpc_fraction"]
            or water_increase > cfg["development"]["feasibility"]["median_relative_irrigation_increase_vs_base_max"]
            or safety > reference_safety
        )
        summaries.append({
            **candidate, "infeasible": infeasible,
            "safety_violation_scenarios": safety,
            "reference_min_safety_violation_scenarios": reference_safety,
            "adverse_types_vs_base": int((by_type_base.drop(index="normal") > tolerance).sum()),
            "improved_types_vs_v2": int((by_type_v2.drop(index="normal") < -tolerance).sum()),
            "normal_non_mpc_fraction_median": normal_non_mpc,
            "median_relative_irrigation_increase_vs_base": water_increase,
            "median_event_deficit": float(current.event_deficit_integral.median()),
            "p90_event_deficit": float(current.event_deficit_integral.quantile(0.9)),
        })
    summary = pd.DataFrame(summaries).sort_values(
        ["infeasible", "safety_violation_scenarios", "adverse_types_vs_base",
         "improved_types_vs_v2", "median_event_deficit",
         "median_relative_irrigation_increase_vs_base"],
        ascending=[True, True, True, False, True, True],
    )
    selected_id = str(summary.iloc[0].candidate_id)
    selected_summary = summary.iloc[0]
    advances = bool(
        selected_summary.infeasible == 0
        and selected_summary.improved_types_vs_v2
        >= cfg["development"]["advancement"]["minimum_improved_types_vs_v2"]
        and selected_summary.adverse_types_vs_base
        <= cfg["development"]["advancement"]["maximum_adverse_types_vs_base"]
    )
    candidate_record = {
        "record_name": "development_candidate_supervision_v3",
        "selected_candidate_id": selected_id,
        "advance_to_confirmation": advances,
        "development_scenarios_used": len(development), "locked_evaluation_scenarios_used": 0,
        **candidate_configs[selected_id],
    }
    metrics_path = project_root / "outputs/runs/supervision_v3_development_metrics.csv"
    summary_path = project_root / "outputs/tables/supervision_v3_candidate_summary.csv"
    candidate_path = project_root / "configs/development_candidate_supervision_v3.yaml"
    metrics.to_csv(metrics_path, index=False)
    summary.to_csv(summary_path, index=False)
    candidate_record["hashes"] = {
        "development_config": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "scenario_manifest": hashlib.sha256(paths["scenarios"].read_bytes()).hexdigest(),
        "anomaly_manifest": hashlib.sha256(paths["anomalies"].read_bytes()).hexdigest(),
    }
    candidate_path.write_text(
        yaml.safe_dump(candidate_record, sort_keys=False), encoding="utf-8"
    )
    result_path = project_root / "data/processed/supervision_v3_development_v1.json"
    result = {
        "development_name": cfg["development"]["name"],
        "classification": (
            "V3_ADVANCES_TO_CONFIRMATION" if advances else "NO_V3_CANDIDATE_ADVANCES"
        ),
        "selected_candidate": json.loads(summary.iloc[[0]].to_json(orient="records"))[0],
        "advance_to_confirmation": advances,
        "scenario_count": len(development), "candidate_count": len(cfg["candidates"]),
        "method_runs": len(metrics), "locked_evaluation_scenarios_used": 0,
        "interpretation": "development_selection_not_confirmatory_evidence",
        "hashes": {
            "metrics": hashlib.sha256(metrics_path.read_bytes()).hexdigest(),
            "candidate_summary": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
            "development_candidate_supervision_v3": hashlib.sha256(
                candidate_path.read_bytes()
            ).hexdigest(),
        },
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics_path, summary_path, candidate_path, result_path
