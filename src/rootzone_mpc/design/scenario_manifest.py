from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


DERIVED_COLUMNS = {
    "saturation",
    "initial_theta",
    "et_stress_start",
}


def _latin_hypercube(count: int, dimensions: int, rng: np.random.Generator) -> np.ndarray:
    samples = np.empty((count, dimensions), dtype=float)
    for dimension in range(dimensions):
        strata = (np.arange(count, dtype=float) + rng.random(count)) / count
        samples[:, dimension] = strata[rng.permutation(count)]
    return samples


def _scale(unit_values: np.ndarray, bounds: list[float]) -> np.ndarray:
    lower, upper = map(float, bounds)
    return lower + unit_values * (upper - lower)


def _build_split(
    split: str,
    prefix: str,
    count: int,
    master_seed: int,
    horizon_hours: int,
    envelope: dict,
    profiles: list[str],
) -> pd.DataFrame:
    rng = np.random.default_rng(master_seed)
    parameter_names = list(envelope)
    unit_samples = _latin_hypercube(count, len(parameter_names), rng)
    values = {
        name: _scale(unit_samples[:, index], envelope[name])
        for index, name in enumerate(parameter_names)
    }
    frame = pd.DataFrame(values)
    frame.insert(0, "scenario_seed", master_seed * 1000 + np.arange(1, count + 1))
    frame.insert(0, "horizon_hours", horizon_hours)
    frame.insert(0, "split", split)
    frame.insert(0, "scenario_id", [f"{prefix}{index:04d}" for index in range(1, count + 1)])

    profile_order = np.resize(np.asarray(profiles, dtype=object), count)
    frame["et_profile"] = profile_order[rng.permutation(count)]
    frame["saturation"] = (
        frame["field_capacity"] + frame["saturation_margin_above_fc"]
    )
    available_range = frame["field_capacity"] - frame["wilting_point"]
    frame["initial_theta"] = frame["wilting_point"] + (
        frame["initial_available_water_fraction"] * available_range
    )
    frame["et_stress_start"] = frame["wilting_point"] + (
        frame["et_stress_fraction_between_wp_fc"] * available_range
    )
    return frame


def validate_scenario_manifest(frame: pd.DataFrame, cfg: dict) -> dict:
    design = cfg["design"]
    expected = {
        "development": int(design["development_count"]),
        "locked_evaluation": int(design["locked_evaluation_count"]),
    }
    actual = frame.groupby("split").size().to_dict()
    errors: list[str] = []
    if actual != expected:
        errors.append(f"split counts differ: expected={expected}, actual={actual}")
    if not frame["scenario_id"].is_unique:
        errors.append("scenario_id is not unique")
    if not frame["scenario_seed"].is_unique:
        errors.append("scenario_seed is not unique")
    if not (frame["wilting_point"] < frame["initial_theta"]).all():
        errors.append("initial_theta must exceed wilting_point")
    if not (frame["initial_theta"] < frame["field_capacity"]).all():
        errors.append("initial_theta must be below field_capacity")
    if not (frame["wilting_point"] < frame["et_stress_start"]).all():
        errors.append("et_stress_start must exceed wilting_point")
    if not (frame["et_stress_start"] < frame["field_capacity"]).all():
        errors.append("et_stress_start must be below field_capacity")
    if not (frame["field_capacity"] < frame["saturation"]).all():
        errors.append("saturation must exceed field_capacity")
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "row_count": int(len(frame)),
        "split_counts": actual,
        "unique_ids": int(frame["scenario_id"].nunique()),
        "unique_seeds": int(frame["scenario_seed"].nunique()),
        "checks_passed": 7,
    }


def build_scenario_manifest(config_path: Path, project_root: Path) -> tuple[Path, Path]:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    design = cfg["design"]
    profiles = cfg["categorical_profiles"]["et_profile"]
    development = _build_split(
        split="development",
        prefix="D",
        count=int(design["development_count"]),
        master_seed=int(design["development_master_seed"]),
        horizon_hours=int(design["horizon_hours"]),
        envelope=cfg["parameter_envelope"],
        profiles=profiles,
    )
    locked = _build_split(
        split="locked_evaluation",
        prefix="E",
        count=int(design["locked_evaluation_count"]),
        master_seed=int(design["locked_evaluation_master_seed"]),
        horizon_hours=int(design["horizon_hours"]),
        envelope=cfg["parameter_envelope"],
        profiles=profiles,
    )
    frame = pd.concat([development, locked], ignore_index=True)
    audit = validate_scenario_manifest(frame, cfg)

    output_dir = project_root / "data" / "processed"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "scenario_manifest_v1.csv"
    audit_path = output_dir / "scenario_manifest_v1.audit.json"
    frame.to_csv(manifest_path, index=False, float_format="%.8f")
    config_display = (
        str(config_path.relative_to(project_root))
        if config_path.is_relative_to(project_root)
        else str(config_path)
    )
    audit.update(
        {
            "design_name": design["name"],
            "config": config_display,
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "parameter_interpretation": cfg["design_notes"]["parameter_interpretation"],
            "locked_evaluation_policy": cfg["design_notes"]["tuning_rule"],
        }
    )
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path, audit_path
