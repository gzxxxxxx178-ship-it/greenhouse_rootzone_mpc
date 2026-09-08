from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rootzone_mpc.experiments.excitation_identifiability import (
    design_matrix,
    fit_known_structure,
    probe_rmse,
    simulate_cycles,
)
from rootzone_mpc.models.two_layer_greybox import TwoLayerGreyBox


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit(project_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project_root, text=True
    ).strip()


def parameter_widths(cfg: dict) -> np.ndarray:
    shallow = cfg["bounds"]["shallow_delta"]
    deep = cfg["bounds"]["deep_delta"]
    return np.concatenate([
        np.asarray(shallow["upper"], dtype=float) - np.asarray(shallow["lower"], dtype=float),
        np.asarray(deep["upper"], dtype=float) - np.asarray(deep["lower"], dtype=float),
    ])


def amplitude_sensitive_information(
    design: np.ndarray, parameter_width: np.ndarray, delta_noise_sd: float
) -> dict[str, float]:
    if delta_noise_sd <= 0:
        raise ValueError("delta_noise_sd must be positive")
    scaled = design * np.asarray(parameter_width, dtype=float)[None, :] / delta_noise_sd
    singular = np.linalg.svd(scaled, compute_uv=False)
    information = scaled.T @ scaled
    covariance = np.linalg.pinv(information, rcond=1e-12)
    return {
        "minimum_singular_value_snr": float(singular[-1]),
        "predicted_normalized_se_rms": float(np.sqrt(np.mean(np.diag(covariance)))),
    }


def _aggregate(frame: pd.DataFrame, condition: str) -> dict:
    selected = frame[frame["condition"] == condition]
    metrics = [
        "information_min_singular_snr", "predicted_normalized_se_rms",
        "normalized_parameter_rmse", "probe_rmse_mean", "active_bound_count",
    ]
    return {
        metric: {
            "median": float(selected[metric].median()),
            "q25": float(selected[metric].quantile(0.25)),
            "q75": float(selected[metric].quantile(0.75)),
        }
        for metric in metrics
    }


def _empirical_variability(frame: pd.DataFrame, condition: str, widths: np.ndarray) -> float:
    selected = frame[frame["condition"] == condition]
    columns = [f"estimated_parameter_{index + 1:02d}" for index in range(len(widths))]
    normalized_sd = selected[columns].std(ddof=1).to_numpy() / widths
    return float(np.sqrt(np.mean(np.square(normalized_sd))))


def _rank_correlation(left: pd.Series, right: pd.Series) -> float:
    return float(left.rank(method="average").corr(right.rank(method="average")))


def run_information_identifiability(project_root: Path) -> tuple[Path, Path]:
    config_path = project_root / "configs/information_identifiability_v1.yaml"
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))["information_identifiability"]
    seeds = list(map(int, cfg["paired_seeds"]))
    if len(seeds) != int(cfg["repetitions"]) or len(set(seeds)) != len(seeds):
        raise ValueError("paired seeds must be unique and match repetitions")
    true_model = TwoLayerGreyBox(
        np.asarray(cfg["true_parameters"]["shallow_delta"]),
        np.asarray(cfg["true_parameters"]["deep_delta"]),
    )
    steps = int(cfg["steps_per_cycle"])
    widths = parameter_widths(cfg)
    delta_noise_sd = float(np.sqrt(
        2 * float(cfg["measurement_noise_sd_m3_m3"]) ** 2
        + float(cfg["process_noise_sd_m3_m3"]) ** 2
    ))
    probe_count = len(cfg["common_probe"]["initial_states"])
    probes = simulate_cycles(
        cfg["common_probe"], true_model, steps=steps,
        process_noise=np.zeros((probe_count, steps, 2)),
        measurement_noise=np.zeros((probe_count, steps + 1, 2)),
    )

    conditions = ["weak", "moderate", "rich"]
    rows = []
    for repetition, seed in enumerate(seeds, start=1):
        rng = np.random.default_rng(seed)
        cycle_count = len(cfg["weak_excitation"]["initial_states"])
        process_noise = rng.normal(
            0, float(cfg["process_noise_sd_m3_m3"]), (cycle_count, steps, 2)
        )
        measurement_noise = rng.normal(
            0, float(cfg["measurement_noise_sd_m3_m3"]), (cycle_count, steps + 1, 2)
        )
        for condition in conditions:
            cycles = simulate_cycles(
                cfg[f"{condition}_excitation"], true_model, steps=steps,
                process_noise=process_noise, measurement_noise=measurement_noise,
            )
            design, _ = design_matrix(cycles)
            layer_information = [
                amplitude_sensitive_information(design, widths[:5], delta_noise_sd),
                amplitude_sensitive_information(design, widths[5:], delta_noise_sd),
            ]
            model, diagnostics = fit_known_structure(cycles, cfg)
            shallow_probe, deep_probe, mean_probe = probe_rmse(model, probes)
            row = {
                "repetition": repetition, "seed": seed, "condition": condition,
                "information_min_singular_snr": min(
                    item["minimum_singular_value_snr"] for item in layer_information
                ),
                "predicted_normalized_se_rms": float(np.sqrt(np.mean([
                    item["predicted_normalized_se_rms"] ** 2 for item in layer_information
                ]))),
                "normalized_parameter_rmse": diagnostics["normalized_parameter_rmse"],
                "active_bound_count": diagnostics["active_bound_count"],
                "solver_converged": diagnostics["converged"],
                "probe_rmse_shallow": shallow_probe,
                "probe_rmse_deep": deep_probe,
                "probe_rmse_mean": mean_probe,
            }
            for index, value in enumerate(diagnostics["estimated_parameters"], start=1):
                row[f"estimated_parameter_{index:02d}"] = value
            rows.append(row)

    frame = pd.DataFrame(rows)
    aggregate = {condition: _aggregate(frame, condition) for condition in conditions}
    variability = {
        condition: _empirical_variability(frame, condition, widths) for condition in conditions
    }
    wide = frame.pivot(index=["repetition", "seed"], columns="condition")
    info_ordering = (
        (wide["information_min_singular_snr"]["weak"]
         < wide["information_min_singular_snr"]["moderate"])
        & (wide["information_min_singular_snr"]["moderate"]
           < wide["information_min_singular_snr"]["rich"])
    )
    uncertainty_ordering = (
        (wide["predicted_normalized_se_rms"]["weak"]
         > wide["predicted_normalized_se_rms"]["moderate"])
        & (wide["predicted_normalized_se_rms"]["moderate"]
           > wide["predicted_normalized_se_rms"]["rich"])
    )
    error_ordering = (
        (wide["normalized_parameter_rmse"]["weak"]
         > wide["normalized_parameter_rmse"]["moderate"])
        & (wide["normalized_parameter_rmse"]["moderate"]
           > wide["normalized_parameter_rmse"]["rich"])
    )
    ratios = {
        "information_min_singular_snr": aggregate["rich"]["information_min_singular_snr"]["median"]
        / aggregate["weak"]["information_min_singular_snr"]["median"],
        "predicted_normalized_se_rms": aggregate["rich"]["predicted_normalized_se_rms"]["median"]
        / aggregate["weak"]["predicted_normalized_se_rms"]["median"],
        "normalized_parameter_rmse": aggregate["rich"]["normalized_parameter_rmse"]["median"]
        / aggregate["weak"]["normalized_parameter_rmse"]["median"],
        "empirical_normalized_parameter_variability": variability["rich"] / variability["weak"],
        "probe_rmse_mean": aggregate["rich"]["probe_rmse_mean"]["median"]
        / aggregate["weak"]["probe_rmse_mean"]["median"],
    }
    spearman = _rank_correlation(
        frame["predicted_normalized_se_rms"], frame["normalized_parameter_rmse"]
    )
    criteria = cfg["success_criteria"]
    truth = np.concatenate([
        np.asarray(cfg["true_parameters"]["shallow_delta"]),
        np.asarray(cfg["true_parameters"]["deep_delta"]),
    ])
    lower = np.concatenate([
        np.asarray(cfg["bounds"]["shallow_delta"]["lower"]),
        np.asarray(cfg["bounds"]["deep_delta"]["lower"]),
    ])
    upper = np.concatenate([
        np.asarray(cfg["bounds"]["shallow_delta"]["upper"]),
        np.asarray(cfg["bounds"]["deep_delta"]["upper"]),
    ])
    ordering = {
        "information": float(info_ordering.mean()),
        "predicted_uncertainty": float(uncertainty_ordering.mean()),
        "actual_parameter_error": float(error_ordering.mean()),
    }
    invariants = {
        "true_parameters_strictly_inside_bounds": bool(np.all(truth > lower) and np.all(truth < upper)),
        "all_solvers_converged": bool(frame["solver_converged"].all()),
        "information_gain_met": ratios["information_min_singular_snr"]
        >= float(criteria["minimum_rich_to_weak_information_ratio"]),
        "predicted_uncertainty_reduction_met": ratios["predicted_normalized_se_rms"]
        <= float(criteria["maximum_rich_to_weak_predicted_uncertainty_ratio"]),
        "information_ordering_met": ordering["information"]
        >= float(criteria["minimum_information_strict_ordering_ratio"]),
        "predicted_uncertainty_ordering_met": ordering["predicted_uncertainty"]
        >= float(criteria["minimum_predicted_uncertainty_strict_ordering_ratio"]),
        "uncertainty_tracks_actual_error": spearman
        >= float(criteria["minimum_uncertainty_error_spearman"]),
        "actual_parameter_error_reduction_met": ratios["normalized_parameter_rmse"]
        <= float(criteria["maximum_rich_to_weak_parameter_error_ratio"]),
        "actual_error_ordering_met": ordering["actual_parameter_error"]
        >= float(criteria["minimum_actual_error_strict_ordering_ratio"]),
        "empirical_variability_reduction_met": ratios["empirical_normalized_parameter_variability"]
        <= float(criteria["maximum_rich_to_weak_empirical_variability_ratio"]),
        "probe_rmse_reduction_met": ratios["probe_rmse_mean"]
        <= float(criteria["maximum_rich_to_weak_probe_rmse_ratio"]),
    }
    table_path = project_root / "outputs/tables/information_identifiability_repetitions_v1.csv"
    table_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(table_path, index=False)
    result = {
        "name": cfg["name"],
        "status": "passed" if all(invariants.values()) else "failed",
        "code_commit": _git_commit(project_root),
        "config_sha256": _sha256(config_path),
        "repetitions": len(seeds),
        "conditions": conditions,
        "fit_cycles_per_condition_per_repetition": cycle_count,
        "common_probe_cycles": probe_count,
        "assumed_delta_observation_noise_sd_m3_m3": delta_noise_sd,
        "aggregate": aggregate,
        "empirical_normalized_parameter_variability": variability,
        "rich_to_weak_median_ratios": ratios,
        "strict_ordering_ratios": ordering,
        "pooled_predicted_uncertainty_actual_error_spearman": spearman,
        "invariants": invariants,
        "evidence_boundary": cfg["evidence_boundary"],
    }
    result_path = project_root / "data/processed/information_identifiability_v1.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return table_path, result_path
