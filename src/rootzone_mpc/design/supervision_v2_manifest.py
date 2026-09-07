from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import yaml

from rootzone_mpc.design.anomaly_manifest import _assign_split, validate_anomaly_manifest
from rootzone_mpc.design.scenario_manifest import _build_split, validate_scenario_manifest


def build_supervision_v2_manifests(project_root: Path) -> tuple[Path, Path, Path]:
    design_path = project_root / "configs/supervision_v2_design.yaml"
    cfg = yaml.safe_load(design_path.read_text(encoding="utf-8"))
    design = cfg["design"]
    base_path = project_root / design["base_envelope_config"]
    anomaly_path = project_root / design["anomaly_magnitude_config"]
    base_cfg = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    anomaly_cfg = yaml.safe_load(anomaly_path.read_text(encoding="utf-8"))
    pieces = []
    for split, prefix, count_key, seed_key in (
        ("development", "V2D", "development_count", "development_master_seed"),
        ("locked_evaluation", "V2E", "locked_evaluation_count", "locked_evaluation_master_seed"),
    ):
        pieces.append(
            _build_split(
                split, prefix, int(design[count_key]), int(design[seed_key]),
                int(design["horizon_hours"]), base_cfg["parameter_envelope"],
                base_cfg["categorical_profiles"]["et_profile"],
            )
        )
    scenarios = pd.concat(pieces, ignore_index=True)
    validation_cfg = {
        "design": {
            "development_count": design["development_count"],
            "locked_evaluation_count": design["locked_evaluation_count"],
        }
    }
    scenario_audit = validate_scenario_manifest(scenarios, validation_cfg)
    anomaly_pieces = []
    for split, seed_key in (
        ("development", "development_assignment_seed"),
        ("locked_evaluation", "locked_assignment_seed"),
    ):
        anomaly_pieces.append(
            _assign_split(
                scenarios[scenarios["split"] == split], anomaly_cfg["anomaly_types"],
                int(design[seed_key]), int(design["default_start_step"]),
                int(design["default_duration_steps"]), anomaly_cfg["magnitudes"],
            )
        )
    anomalies = pd.concat(anomaly_pieces, ignore_index=True)
    anomaly_audit = validate_anomaly_manifest(anomalies, anomaly_cfg["anomaly_types"])
    out = project_root / "data/processed"
    scenario_out = out / "supervision_v2_scenario_manifest.csv"
    anomaly_out = out / "supervision_v2_anomaly_manifest.csv"
    audit_out = out / "supervision_v2_manifest.audit.json"
    scenarios.to_csv(scenario_out, index=False, float_format="%.8f")
    anomalies.to_csv(anomaly_out, index=False)
    audit = {
        "design_name": design["name"], "scenario_validation": scenario_audit,
        "anomaly_validation": anomaly_audit, "locked_evaluation_scenarios_used": 0,
        "tuning_rule": design["tuning_rule"],
        "hashes": {
            "design_config": hashlib.sha256(design_path.read_bytes()).hexdigest(),
            "base_envelope_config": hashlib.sha256(base_path.read_bytes()).hexdigest(),
            "anomaly_magnitude_config": hashlib.sha256(anomaly_path.read_bytes()).hexdigest(),
            "scenario_manifest": hashlib.sha256(scenario_out.read_bytes()).hexdigest(),
            "anomaly_manifest": hashlib.sha256(anomaly_out.read_bytes()).hexdigest(),
        },
    }
    audit_out.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return scenario_out, anomaly_out, audit_out
