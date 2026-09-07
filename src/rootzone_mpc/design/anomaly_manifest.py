from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


def _assign_split(
    base: pd.DataFrame,
    anomaly_types: list[str],
    assignment_seed: int,
    default_start: int,
    default_duration: int,
    magnitudes: dict,
) -> pd.DataFrame:
    if len(base) % len(anomaly_types) != 0:
        raise ValueError("Base scenario count must be divisible by anomaly type count")
    rng = np.random.default_rng(assignment_seed)
    assignments = np.tile(np.asarray(anomaly_types, dtype=object), len(base) // len(anomaly_types))
    assignments = assignments[rng.permutation(len(base))]
    rows = []
    for (_, scenario), anomaly_type in zip(base.iterrows(), assignments):
        duration = default_duration
        if anomaly_type == "sensor_missing":
            duration = int(magnitudes["sensor_missing_duration_steps"])
        elif anomaly_type == "sensor_spike":
            duration = int(magnitudes["sensor_spike_duration_steps"])
        elif anomaly_type == "solver_failure":
            duration = int(magnitudes["solver_failure_duration_steps"])
        elif anomaly_type == "feedback_conflict":
            duration = int(magnitudes["feedback_conflict_duration_steps"])
        rows.append(
            {
                "anomaly_scenario_id": f"A{scenario['scenario_id']}",
                "base_scenario_id": scenario["scenario_id"],
                "split": scenario["split"],
                "anomaly_type": anomaly_type,
                "anomaly_seed": int(scenario["scenario_seed"]) + 707,
                "start_step": default_start,
                "end_step": default_start + duration,
            }
        )
    return pd.DataFrame(rows)


def validate_anomaly_manifest(frame: pd.DataFrame, anomaly_types: list[str]) -> dict:
    errors = []
    if not frame["anomaly_scenario_id"].is_unique:
        errors.append("anomaly_scenario_id is not unique")
    if set(frame["anomaly_type"]) != set(anomaly_types):
        errors.append("anomaly type coverage differs from design")
    counts = frame.groupby(["split", "anomaly_type"]).size()
    for split, group in counts.groupby(level=0):
        if group.nunique() != 1:
            errors.append(f"anomaly types are not balanced within {split}")
    if not (frame["end_step"] > frame["start_step"]).all():
        errors.append("all anomaly schedules require positive duration")
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "row_count": int(len(frame)),
        "checks_passed": 4,
        "split_counts": frame.groupby("split").size().to_dict(),
        "counts_by_split_and_type": {
            f"{split}/{kind}": int(value) for (split, kind), value in counts.items()
        },
    }


def build_anomaly_manifest(project_root: Path) -> tuple[Path, Path]:
    config_path = project_root / "configs" / "anomaly_design_v1.yaml"
    scenario_path = project_root / "data" / "processed" / "scenario_manifest_v1.csv"
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    scenarios = pd.read_csv(scenario_path)
    anomaly_types = cfg["anomaly_types"]
    design = cfg["design"]
    pieces = []
    for split, seed_key in (
        ("development", "development_assignment_seed"),
        ("locked_evaluation", "locked_assignment_seed"),
    ):
        pieces.append(
            _assign_split(
                scenarios[scenarios["split"] == split],
                anomaly_types,
                int(design[seed_key]),
                int(design["default_start_step"]),
                int(design["default_duration_steps"]),
                cfg["magnitudes"],
            )
        )
    frame = pd.concat(pieces, ignore_index=True)
    audit = validate_anomaly_manifest(frame, anomaly_types)
    output_dir = project_root / "data" / "processed"
    manifest_path = output_dir / "anomaly_manifest_v1.csv"
    audit_path = output_dir / "anomaly_manifest_v1.audit.json"
    frame.to_csv(manifest_path, index=False)
    audit.update(
        {
            "design_name": design["name"],
            "interpretation": design["interpretation"],
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "base_manifest_sha256": hashlib.sha256(scenario_path.read_bytes()).hexdigest(),
            "anomaly_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        }
    )
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path, audit_path
