from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rootzone_mpc.models.two_layer_greybox import TwoLayerGreyBox, bounded_least_squares


@dataclass(frozen=True)
class SyntheticCycle:
    true_states: np.ndarray
    observed_states: np.ndarray
    irrigation_mm: np.ndarray
    solar_w_m2: np.ndarray
    vpd_kpa: np.ndarray


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit(project_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project_root, text=True
    ).strip()


def _inputs(design: dict, cycle_index: int, steps: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    irrigation = np.zeros(steps)
    for step, amount in design["irrigation_pulses"][cycle_index]:
        irrigation[int(step)] = float(amount)
    amplitude, phase, vpd_base, vpd_amplitude, vpd_phase = map(
        float, design["weather"][cycle_index]
    )
    time_index = np.arange(steps, dtype=float)
    solar = amplitude * np.maximum(0.0, np.sin(np.pi * time_index / steps + phase))
    vpd = vpd_base + vpd_amplitude * np.sin(2 * np.pi * time_index / steps + vpd_phase)
    return irrigation, solar, np.maximum(vpd, 0.05)


def simulate_cycles(
    design: dict,
    true_model: TwoLayerGreyBox,
    *,
    steps: int,
    process_noise: np.ndarray,
    measurement_noise: np.ndarray,
) -> list[SyntheticCycle]:
    cycle_count = len(design["initial_states"])
    if process_noise.shape != (cycle_count, steps, 2):
        raise ValueError("process_noise has an incompatible shape")
    if measurement_noise.shape != (cycle_count, steps + 1, 2):
        raise ValueError("measurement_noise has an incompatible shape")
    cycles = []
    for cycle_index, initial in enumerate(design["initial_states"]):
        irrigation, solar, vpd = _inputs(design, cycle_index, steps)
        true_states = np.empty((steps + 1, 2), dtype=float)
        true_states[0] = np.asarray(initial, dtype=float)
        for step in range(steps):
            predicted = true_model.predict_next(
                true_states[step, 0], true_states[step, 1], irrigation[step],
                solar[step], vpd[step],
            )
            true_states[step + 1] = np.asarray(predicted) + process_noise[cycle_index, step]
        observed_states = true_states + measurement_noise[cycle_index]
        cycles.append(SyntheticCycle(true_states, observed_states, irrigation, solar, vpd))
    return cycles


def design_matrix(cycles: list[SyntheticCycle]) -> tuple[np.ndarray, np.ndarray]:
    rows = []
    targets = []
    for cycle in cycles:
        for step in range(len(cycle.irrigation_mm)):
            current = cycle.observed_states[step]
            following = cycle.observed_states[step + 1]
            rows.append([
                current[0] - current[1], cycle.irrigation_mm[step],
                cycle.solar_w_m2[step] / 1000.0, cycle.vpd_kpa[step], 1.0,
            ])
            targets.append(following - current)
    return np.asarray(rows), np.asarray(targets)


def standardized_condition_number(design: np.ndarray) -> float:
    transformed = design.copy().astype(float)
    for column in range(transformed.shape[1] - 1):
        scale = float(np.std(transformed[:, column]))
        if scale <= 1e-12:
            return float("inf")
        transformed[:, column] = (
            transformed[:, column] - np.mean(transformed[:, column])
        ) / scale
    singular_values = np.linalg.svd(transformed, compute_uv=False)
    if singular_values[-1] <= 1e-12:
        return float("inf")
    return float(singular_values[0] / singular_values[-1])


def fit_known_structure(cycles: list[SyntheticCycle], cfg: dict) -> tuple[TwoLayerGreyBox, dict]:
    design, target = design_matrix(cycles)
    fits = []
    for layer_name, layer_index in [("shallow_delta", 0), ("deep_delta", 1)]:
        bound = cfg["bounds"][layer_name]
        fits.append(bounded_least_squares(
            design, target[:, layer_index],
            np.asarray(bound["lower"]), np.asarray(bound["upper"]),
            max_iterations=int(cfg["solver"]["max_iterations"]),
            tolerance=float(cfg["solver"]["tolerance"]),
        ))
    model = TwoLayerGreyBox(fits[0].coefficients, fits[1].coefficients)
    estimated = np.concatenate([fits[0].coefficients, fits[1].coefficients])
    truth = np.concatenate([
        np.asarray(cfg["true_parameters"]["shallow_delta"]),
        np.asarray(cfg["true_parameters"]["deep_delta"]),
    ])
    widths = np.concatenate([
        np.asarray(cfg["bounds"]["shallow_delta"]["upper"])
        - np.asarray(cfg["bounds"]["shallow_delta"]["lower"]),
        np.asarray(cfg["bounds"]["deep_delta"]["upper"])
        - np.asarray(cfg["bounds"]["deep_delta"]["lower"]),
    ])
    lower = np.concatenate([
        np.asarray(cfg["bounds"]["shallow_delta"]["lower"]),
        np.asarray(cfg["bounds"]["deep_delta"]["lower"]),
    ])
    upper = np.concatenate([
        np.asarray(cfg["bounds"]["shallow_delta"]["upper"]),
        np.asarray(cfg["bounds"]["deep_delta"]["upper"]),
    ])
    diagnostics = {
        "condition_number": standardized_condition_number(design),
        "normalized_parameter_rmse": float(np.sqrt(np.mean(((estimated - truth) / widths) ** 2))),
        "active_bound_count": int(np.sum(
            np.isclose(estimated, lower, atol=1e-8) | np.isclose(estimated, upper, atol=1e-8)
        )),
        "converged": bool(all(fit.converged for fit in fits)),
        "estimated_parameters": estimated,
    }
    return model, diagnostics


def probe_rmse(model: TwoLayerGreyBox, probe_cycles: list[SyntheticCycle]) -> tuple[float, float, float]:
    residuals = [[], []]
    for cycle in probe_cycles:
        predicted = cycle.true_states[0].copy()
        for step in range(len(cycle.irrigation_mm)):
            predicted = np.asarray(model.predict_next(
                predicted[0], predicted[1], cycle.irrigation_mm[step],
                cycle.solar_w_m2[step], cycle.vpd_kpa[step],
            ))
            error = predicted - cycle.true_states[step + 1]
            residuals[0].append(error[0])
            residuals[1].append(error[1])
    shallow = float(np.sqrt(np.mean(np.square(residuals[0]))))
    deep = float(np.sqrt(np.mean(np.square(residuals[1]))))
    return shallow, deep, float(np.mean([shallow, deep]))


def _aggregate(frame: pd.DataFrame, condition: str) -> dict:
    selected = frame[frame["condition"] == condition]
    return {
        metric: {
            "median": float(selected[metric].median()),
            "q25": float(selected[metric].quantile(0.25)),
            "q75": float(selected[metric].quantile(0.75)),
        }
        for metric in [
            "condition_number", "normalized_parameter_rmse", "active_bound_count",
            "probe_rmse_shallow", "probe_rmse_deep", "probe_rmse_mean",
        ]
    }


def run_excitation_identifiability(project_root: Path) -> tuple[Path, Path]:
    config_path = project_root / "configs/excitation_identifiability_v1.yaml"
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))["excitation_identifiability"]
    seeds = list(map(int, cfg["paired_seeds"]))
    if len(seeds) != int(cfg["repetitions"]) or len(set(seeds)) != len(seeds):
        raise ValueError("paired seeds must be unique and match repetitions")
    true_model = TwoLayerGreyBox(
        np.asarray(cfg["true_parameters"]["shallow_delta"]),
        np.asarray(cfg["true_parameters"]["deep_delta"]),
    )
    steps = int(cfg["steps_per_cycle"])
    zero_process = np.zeros((len(cfg["common_probe"]["initial_states"]), steps, 2))
    zero_measurement = np.zeros((len(cfg["common_probe"]["initial_states"]), steps + 1, 2))
    probes = simulate_cycles(
        cfg["common_probe"], true_model, steps=steps,
        process_noise=zero_process, measurement_noise=zero_measurement,
    )

    rows = []
    feature_count = len(cfg["feature_names"])
    for repetition, seed in enumerate(seeds, start=1):
        rng = np.random.default_rng(seed)
        fit_cycle_count = len(cfg["weak_excitation"]["initial_states"])
        process_noise = rng.normal(
            0, float(cfg["process_noise_sd_m3_m3"]), (fit_cycle_count, steps, 2)
        )
        measurement_noise = rng.normal(
            0, float(cfg["measurement_noise_sd_m3_m3"]), (fit_cycle_count, steps + 1, 2)
        )
        for condition, design in [
            ("weak", cfg["weak_excitation"]), ("rich", cfg["rich_excitation"]),
        ]:
            cycles = simulate_cycles(
                design, true_model, steps=steps,
                process_noise=process_noise, measurement_noise=measurement_noise,
            )
            model, diagnostic = fit_known_structure(cycles, cfg)
            shallow_rmse, deep_rmse, mean_rmse = probe_rmse(model, probes)
            row = {
                "repetition": repetition, "seed": seed, "condition": condition,
                "condition_number": diagnostic["condition_number"],
                "normalized_parameter_rmse": diagnostic["normalized_parameter_rmse"],
                "active_bound_count": diagnostic["active_bound_count"],
                "solver_converged": diagnostic["converged"],
                "probe_rmse_shallow": shallow_rmse,
                "probe_rmse_deep": deep_rmse,
                "probe_rmse_mean": mean_rmse,
            }
            for index in range(feature_count * 2):
                row[f"estimated_parameter_{index + 1:02d}"] = diagnostic["estimated_parameters"][index]
            rows.append(row)
    frame = pd.DataFrame(rows)
    wide = frame.pivot(index=["repetition", "seed"], columns="condition")
    parameter_improvement = (
        wide["normalized_parameter_rmse"]["rich"]
        < wide["normalized_parameter_rmse"]["weak"]
    )
    probe_improvement = wide["probe_rmse_mean"]["rich"] < wide["probe_rmse_mean"]["weak"]
    aggregate = {condition: _aggregate(frame, condition) for condition in ["weak", "rich"]}
    ratios = {
        "condition_number": aggregate["rich"]["condition_number"]["median"]
        / aggregate["weak"]["condition_number"]["median"],
        "normalized_parameter_rmse": aggregate["rich"]["normalized_parameter_rmse"]["median"]
        / aggregate["weak"]["normalized_parameter_rmse"]["median"],
        "probe_rmse_mean": aggregate["rich"]["probe_rmse_mean"]["median"]
        / aggregate["weak"]["probe_rmse_mean"]["median"],
    }
    criteria = cfg["success_criteria"]
    true_parameters = np.concatenate([
        np.asarray(cfg["true_parameters"]["shallow_delta"]),
        np.asarray(cfg["true_parameters"]["deep_delta"]),
    ])
    lower_bounds = np.concatenate([
        np.asarray(cfg["bounds"]["shallow_delta"]["lower"]),
        np.asarray(cfg["bounds"]["deep_delta"]["lower"]),
    ])
    upper_bounds = np.concatenate([
        np.asarray(cfg["bounds"]["shallow_delta"]["upper"]),
        np.asarray(cfg["bounds"]["deep_delta"]["upper"]),
    ])
    invariants = {
        "true_parameters_strictly_inside_bounds": bool(
            np.all(true_parameters > lower_bounds) and np.all(true_parameters < upper_bounds)
        ),
        "all_solvers_converged": bool(frame["solver_converged"].all()),
        "rich_condition_number_reduced": ratios["condition_number"]
        <= float(criteria["maximum_rich_to_weak_condition_number_ratio"]),
        "rich_parameter_error_reduced": ratios["normalized_parameter_rmse"]
        <= float(criteria["maximum_rich_to_weak_parameter_error_ratio"]),
        "paired_parameter_improvement_ratio_met": float(parameter_improvement.mean())
        >= float(criteria["minimum_parameter_error_improvement_ratio"]),
        "rich_probe_rmse_reduced": ratios["probe_rmse_mean"]
        <= float(criteria["maximum_rich_to_weak_probe_rmse_ratio"]),
        "paired_probe_improvement_ratio_met": float(probe_improvement.mean())
        >= float(criteria["minimum_probe_rmse_improvement_ratio"]),
        "rich_active_bounds_limit_met": aggregate["rich"]["active_bound_count"]["median"]
        <= float(criteria["maximum_rich_median_active_bound_count"]),
    }
    table_path = project_root / "outputs/tables/excitation_identifiability_repetitions_v1.csv"
    table_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(table_path, index=False)
    result = {
        "name": cfg["name"],
        "status": "passed" if all(invariants.values()) else "failed",
        "code_commit": _git_commit(project_root),
        "config_sha256": _sha256(config_path),
        "repetitions": len(seeds),
        "fit_cycles_per_condition_per_repetition": fit_cycle_count,
        "common_probe_cycles": len(probes),
        "aggregate": aggregate,
        "rich_to_weak_median_ratios": ratios,
        "paired_parameter_improvement_ratio": float(parameter_improvement.mean()),
        "paired_probe_improvement_ratio": float(probe_improvement.mean()),
        "invariants": invariants,
        "evidence_boundary": cfg["evidence_boundary"],
    }
    result_path = project_root / "data/processed/excitation_identifiability_v1.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return table_path, result_path
