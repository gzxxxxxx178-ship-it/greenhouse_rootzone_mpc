from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rootzone_mpc.experiments.confirmation import _bootstrap_median_interval
from rootzone_mpc.experiments.two_layer_bridge_data import load_protocol
from rootzone_mpc.experiments.two_layer_control_development import (
    _load_fitted_model,
    run_control_scenario,
)


PAIR_METRICS = (
    "shallow_deficit_integral",
    "deep_deficit_integral",
    "weighted_deficit_integral",
    "weighted_excess_integral",
    "irrigation_command_total_mm",
    "irrigation_delivered_total_mm",
    "deep_drainage_total_mm",
    "action_change_count",
    "shallow_safety_violation_steps",
    "deep_safety_violation_steps",
    "safety_violation_steps",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit(project_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project_root, text=True
    ).strip()


def _load_confirmation(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["two_layer_confirmation"]


def _selected_candidate(protocol: dict, kind: str, candidate_id: str) -> dict:
    matches = [
        candidate for candidate in protocol["development_candidates"][kind]
        if candidate["candidate_id"] == candidate_id
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one frozen {kind} candidate {candidate_id}")
    return matches[0]


def _validate_inputs(
    project_root: Path,
    protocol_path: Path,
    scenario_path: Path,
    model_path: Path,
    frozen: dict,
    development: dict,
    confirmation: dict,
) -> None:
    if frozen.get("status") != "frozen":
        raise ValueError("Two-layer controllers are not frozen")
    if frozen.get("selected_on_split") != "development":
        raise ValueError("Controllers were not selected on development split")
    if frozen.get("locked_evaluation_accessed") is not False:
        raise ValueError("Frozen metadata does not preserve locked isolation")
    if development.get("status") != "passed":
        raise ValueError("Development selection did not pass")
    if int(development.get("locked_evaluation_scenarios_used", -1)) != 0:
        raise ValueError("Development selection accessed locked scenarios")
    if frozen["rule"]["candidate_id"] != confirmation["required_rule_candidate_id"]:
        raise ValueError("Frozen rule ID differs from confirmation contract")
    if frozen["mpc"]["candidate_id"] != confirmation["required_mpc_candidate_id"]:
        raise ValueError("Frozen MPC ID differs from confirmation contract")
    expected_hashes = {
        protocol_path: frozen["protocol_config_sha256"],
        scenario_path: frozen["scenario_manifest_sha256"],
        model_path: frozen["model_result_sha256"],
    }
    mismatches = [str(path.relative_to(project_root)) for path, expected in expected_hashes.items()
                  if _sha256(path) != expected]
    if mismatches:
        raise ValueError(f"Frozen input hash mismatch: {mismatches}")


def _paired_results(raw: pd.DataFrame, scenarios: pd.DataFrame) -> pd.DataFrame:
    metadata = scenarios.set_index("scenario_id")
    pairs = metadata.copy()
    for metric in PAIR_METRICS:
        wide = raw.pivot(index="scenario_id", columns="controller", values=metric)
        pairs[f"rule_{metric}"] = wide["rule"]
        pairs[f"mpc_{metric}"] = wide["mpc"]
        pairs[f"delta_{metric}"] = wide["mpc"] - wide["rule"]
    return pairs.reset_index()


def _metric_summary(
    values: np.ndarray,
    tolerance: float,
    bootstrap_resamples: int | None = None,
    bootstrap_seed: int | None = None,
) -> dict:
    result = {
        "paired_median": float(np.median(values)),
        "paired_p10": float(np.quantile(values, 0.10)),
        "paired_p90": float(np.quantile(values, 0.90)),
        "improved_ratio": float(np.mean(values < -tolerance)),
        "equivalent_ratio": float(np.mean(np.abs(values) <= tolerance)),
        "adverse_ratio": float(np.mean(values > tolerance)),
    }
    if bootstrap_resamples is not None and bootstrap_seed is not None:
        lower, upper = _bootstrap_median_interval(
            values, bootstrap_resamples, bootstrap_seed
        )
        result.update({"ci95_lower": lower, "ci95_upper": upper})
    return result


def classify_two_layer_confirmation(
    pairs: pd.DataFrame, confirmation: dict, aggregate: dict
) -> tuple[str, dict]:
    tie = confirmation["tie_tolerance"]
    deficit_tol = float(tie["weighted_deficit_integral"])
    water_tol = float(tie["irrigation_command_total_mm"])
    deficit = pairs["delta_weighted_deficit_integral"].to_numpy(dtype=float)
    water = pairs["delta_irrigation_command_total_mm"].to_numpy(dtype=float)
    joint_nonworse = (deficit <= deficit_tol) & (water <= water_tol)
    any_adverse = (deficit > deficit_tol) | (water > water_tol)
    rule_safety = int((pairs["rule_safety_violation_steps"] > 0).sum())
    mpc_safety = int((pairs["mpc_safety_violation_steps"] > 0).sum())
    no_extra_safety = mpc_safety <= rule_safety
    median_deficit = float(np.median(deficit))
    median_water = float(np.median(water))
    deficit_upper = float(aggregate["weighted_deficit_integral"]["ci95_upper"])
    water_upper = float(aggregate["irrigation_command_total_mm"]["ci95_upper"])
    joint_ratio = float(np.mean(joint_nonworse))
    adverse_ratio = float(np.mean(any_adverse))

    if (
        median_deficit <= deficit_tol
        and median_water <= water_tol
        and (deficit_upper < -deficit_tol or water_upper < -water_tol)
        and no_extra_safety
        and joint_ratio >= float(confirmation["joint_nonworse_ratio_min"])
        and adverse_ratio <= float(confirmation["any_primary_adverse_ratio_max"])
    ):
        label = "JOINT_VALUE_SUPPORTED"
    elif water_upper < -water_tol and median_deficit > deficit_tol and no_extra_safety:
        label = "WATER_SAVING_WITH_DEFICIT_TRADEOFF"
    elif deficit_upper < -deficit_tol and median_water > water_tol and no_extra_safety:
        label = "DEFICIT_REDUCTION_WITH_WATER_COST"
    else:
        label = "OVERALL_VALUE_NOT_SUPPORTED"
    if label not in confirmation["classification_labels"]:
        raise ValueError("Classification is outside frozen label set")
    diagnostics = {
        "joint_nonworse_ratio": joint_ratio,
        "any_primary_adverse_ratio": adverse_ratio,
        "rule_safety_violation_scenarios": rule_safety,
        "mpc_safety_violation_scenarios": mpc_safety,
        "no_extra_safety_violation": no_extra_safety,
        "rule_shallow_safety_violation_scenarios": int(
            (pairs["rule_shallow_safety_violation_steps"] > 0).sum()
        ),
        "mpc_shallow_safety_violation_scenarios": int(
            (pairs["mpc_shallow_safety_violation_steps"] > 0).sum()
        ),
        "rule_deep_safety_violation_scenarios": int(
            (pairs["rule_deep_safety_violation_steps"] > 0).sum()
        ),
        "mpc_deep_safety_violation_scenarios": int(
            (pairs["mpc_deep_safety_violation_steps"] > 0).sum()
        ),
    }
    return label, diagnostics


def run_two_layer_confirmation(project_root: Path) -> tuple[Path, ...]:
    protocol_path = project_root / "configs/two_layer_control_development_v1.yaml"
    confirmation_path = project_root / "configs/two_layer_confirmation_v1.yaml"
    frozen_path = project_root / "configs/two_layer_frozen_controller_v1.yaml"
    scenario_path = project_root / "data/processed/two_layer_control_scenarios_v1.csv"
    model_path = project_root / "data/processed/two_layer_bridge_identification_v1.json"
    development_path = project_root / "data/processed/two_layer_control_development_v1.json"

    protocol = load_protocol(protocol_path)
    confirmation = _load_confirmation(confirmation_path)
    frozen = yaml.safe_load(frozen_path.read_text(encoding="utf-8"))[
        "two_layer_frozen_controller"
    ]
    development = json.loads(development_path.read_text(encoding="utf-8"))
    model_result = json.loads(model_path.read_text(encoding="utf-8"))
    _validate_inputs(
        project_root, protocol_path, scenario_path, model_path,
        frozen, development, confirmation,
    )
    if model_result.get("status") != "passed":
        raise ValueError("Frozen model validation is not passed")

    scenarios = pd.read_csv(scenario_path)
    locked = scenarios[scenarios["split"] == confirmation["split"]].copy()
    expected = int(confirmation["expected_scenario_count"])
    if len(locked) != expected:
        raise ValueError(f"Expected {expected} locked scenarios, received {len(locked)}")
    model = _load_fitted_model(model_result)
    candidates = {
        kind: _selected_candidate(protocol, kind, frozen[kind]["candidate_id"])
        for kind in ("rule", "mpc")
    }
    rows = []
    for _, scenario in locked.iterrows():
        for kind in ("rule", "mpc"):
            rows.append(run_control_scenario(
                scenario, kind, candidates[kind], protocol, model
            ))
    raw = pd.DataFrame(rows)
    pairs = _paired_results(raw, locked)

    resamples = int(confirmation["bootstrap_resamples"])
    bootstrap_seed = int(confirmation["bootstrap_seed"])
    primary = set(confirmation["primary_endpoints"])
    aggregate = {}
    for index, metric in enumerate(PAIR_METRICS):
        values = pairs[f"delta_{metric}"].to_numpy(dtype=float)
        tolerance = float(confirmation["tie_tolerance"].get(metric, 0.0))
        aggregate[metric] = _metric_summary(
            values,
            tolerance,
            resamples if metric in primary else None,
            bootstrap_seed + index if metric in primary else None,
        )
    classification, diagnostics = classify_two_layer_confirmation(
        pairs, confirmation, aggregate
    )

    run_dir = project_root / "outputs/runs"
    table_dir = project_root / "outputs/tables"
    run_dir.mkdir(parents=True, exist_ok=True)
    table_dir.mkdir(parents=True, exist_ok=True)
    raw_path = run_dir / "two_layer_confirmation_runs_v1.csv"
    pairs_path = table_dir / "two_layer_confirmation_pairs_v1.csv"
    result_path = project_root / "data/processed/two_layer_confirmation_v1.json"
    raw.to_csv(raw_path, index=False)
    pairs.to_csv(pairs_path, index=False)
    result = {
        "name": confirmation["name"],
        "status": "completed",
        "classification": classification,
        "code_commit": _git_commit(project_root),
        "scenario_count": expected,
        "method_runs": int(len(raw)),
        "development_scenarios_used": 0,
        "locked_evaluation_scenarios_used": expected,
        "frozen_candidates": {
            "rule": frozen["rule"]["candidate_id"],
            "mpc": frozen["mpc"]["candidate_id"],
        },
        "aggregate": aggregate,
        "diagnostics": diagnostics,
        "maximum_absolute_water_balance_residual_mm": float(
            raw["max_abs_balance_residual_mm"].max()
        ),
        "bootstrap_resamples": resamples,
        "hashes": {
            "confirmation_config": _sha256(confirmation_path),
            "control_development_config": _sha256(protocol_path),
            "frozen_controller": _sha256(frozen_path),
            "model_result": _sha256(model_path),
            "scenario_manifest": _sha256(scenario_path),
            "paired_results": _sha256(pairs_path),
        },
        "evidence_boundary": confirmation["evidence_boundary"],
    }
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return raw_path, pairs_path, result_path
