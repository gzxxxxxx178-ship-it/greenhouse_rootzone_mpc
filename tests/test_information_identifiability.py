from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rootzone_mpc.experiments.excitation_identifiability import design_matrix, simulate_cycles
from rootzone_mpc.experiments.information_identifiability import (
    _empirical_variability,
    amplitude_sensitive_information,
    parameter_widths,
)
from rootzone_mpc.models.two_layer_greybox import TwoLayerGreyBox


ROOT = Path(__file__).resolve().parents[1]


def configuration() -> dict:
    return yaml.safe_load(
        (ROOT / "configs/information_identifiability_v1.yaml").read_text()
    )["information_identifiability"]


def test_information_metric_retains_feature_amplitude():
    rng = np.random.default_rng(4)
    design = np.column_stack([rng.normal(size=(80, 4)), np.ones(80)])
    widths = np.ones(5)
    baseline = amplitude_sensitive_information(design, widths, 1.0)
    amplified = amplitude_sensitive_information(design, widths * 2.0, 1.0)
    assert np.isclose(
        amplified["minimum_singular_value_snr"],
        2 * baseline["minimum_singular_value_snr"],
    )
    assert np.isclose(
        amplified["predicted_normalized_se_rms"],
        0.5 * baseline["predicted_normalized_se_rms"],
    )


def test_repeated_design_reduces_predicted_uncertainty():
    rng = np.random.default_rng(5)
    design = np.column_stack([rng.normal(size=(80, 4)), np.ones(80)])
    baseline = amplitude_sensitive_information(design, np.ones(5), 1.0)
    repeated = amplitude_sensitive_information(np.tile(design, (2, 1)), np.ones(5), 1.0)
    assert repeated["predicted_normalized_se_rms"] < baseline["predicted_normalized_se_rms"]


def test_three_designs_generate_equal_independent_cycle_counts():
    cfg = configuration()
    model = TwoLayerGreyBox(
        np.asarray(cfg["true_parameters"]["shallow_delta"]),
        np.asarray(cfg["true_parameters"]["deep_delta"]),
    )
    shapes = []
    for condition in ["weak", "moderate", "rich"]:
        cycles = simulate_cycles(
            cfg[f"{condition}_excitation"], model, steps=cfg["steps_per_cycle"],
            process_noise=np.zeros((3, cfg["steps_per_cycle"], 2)),
            measurement_noise=np.zeros((3, cfg["steps_per_cycle"] + 1, 2)),
        )
        design, target = design_matrix(cycles)
        shapes.append((design.shape, target.shape))
    assert shapes == [((144, 5), (144, 2))] * 3


def test_parameter_widths_are_positive():
    widths = parameter_widths(configuration())
    assert widths.shape == (10,)
    assert np.all(widths > 0)


def test_empirical_variability_uses_all_ten_parameter_widths():
    widths = parameter_widths(configuration())
    rows = []
    for repetition in range(3):
        row = {"condition": "weak"}
        for index, width in enumerate(widths, start=1):
            row[f"estimated_parameter_{index:02d}"] = repetition * width
        rows.append(row)
    assert np.isclose(_empirical_variability(pd.DataFrame(rows), "weak", widths), 1.0)
