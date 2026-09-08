from pathlib import Path

import numpy as np
import yaml

from rootzone_mpc.experiments.excitation_identifiability import (
    design_matrix,
    fit_known_structure,
    simulate_cycles,
    standardized_condition_number,
)
from rootzone_mpc.models.two_layer_greybox import TwoLayerGreyBox


ROOT = Path(__file__).resolve().parents[1]


def configuration() -> dict:
    return yaml.safe_load(
        (ROOT / "configs/excitation_identifiability_v1.yaml").read_text()
    )["excitation_identifiability"]


def test_synthetic_cycle_generation_is_deterministic():
    cfg = configuration()
    model = TwoLayerGreyBox(
        np.asarray(cfg["true_parameters"]["shallow_delta"]),
        np.asarray(cfg["true_parameters"]["deep_delta"]),
    )
    shape_process = (3, cfg["steps_per_cycle"], 2)
    shape_measurement = (3, cfg["steps_per_cycle"] + 1, 2)
    first = simulate_cycles(
        cfg["rich_excitation"], model, steps=cfg["steps_per_cycle"],
        process_noise=np.zeros(shape_process), measurement_noise=np.zeros(shape_measurement),
    )
    second = simulate_cycles(
        cfg["rich_excitation"], model, steps=cfg["steps_per_cycle"],
        process_noise=np.zeros(shape_process), measurement_noise=np.zeros(shape_measurement),
    )
    np.testing.assert_array_equal(first[0].true_states, second[0].true_states)


def test_noiseless_rich_design_recovers_known_parameters():
    cfg = configuration()
    model = TwoLayerGreyBox(
        np.asarray(cfg["true_parameters"]["shallow_delta"]),
        np.asarray(cfg["true_parameters"]["deep_delta"]),
    )
    cycles = simulate_cycles(
        cfg["rich_excitation"], model, steps=cfg["steps_per_cycle"],
        process_noise=np.zeros((3, cfg["steps_per_cycle"], 2)),
        measurement_noise=np.zeros((3, cfg["steps_per_cycle"] + 1, 2)),
    )
    fitted, diagnostics = fit_known_structure(cycles, cfg)
    np.testing.assert_allclose(
        fitted.shallow_delta_coefficients,
        cfg["true_parameters"]["shallow_delta"], atol=1e-8,
    )
    np.testing.assert_allclose(
        fitted.deep_delta_coefficients,
        cfg["true_parameters"]["deep_delta"], atol=1e-8,
    )
    assert diagnostics["active_bound_count"] == 0


def test_condition_number_rejects_constant_feature():
    design = np.column_stack([np.arange(10), np.ones(10), np.arange(10), np.arange(10), np.ones(10)])
    assert standardized_condition_number(design) == float("inf")


def test_design_matrix_does_not_cross_cycle_boundaries():
    cfg = configuration()
    model = TwoLayerGreyBox(
        np.asarray(cfg["true_parameters"]["shallow_delta"]),
        np.asarray(cfg["true_parameters"]["deep_delta"]),
    )
    cycles = simulate_cycles(
        cfg["weak_excitation"], model, steps=cfg["steps_per_cycle"],
        process_noise=np.zeros((3, cfg["steps_per_cycle"], 2)),
        measurement_noise=np.zeros((3, cfg["steps_per_cycle"] + 1, 2)),
    )
    design, target = design_matrix(cycles)
    assert design.shape == (3 * cfg["steps_per_cycle"], 5)
    assert target.shape == (3 * cfg["steps_per_cycle"], 2)
