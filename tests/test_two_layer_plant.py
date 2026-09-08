from pathlib import Path

import numpy as np
import pandas as pd

from rootzone_mpc.data.field_quality import load_quality_config, validate_field_data
from rootzone_mpc.data.model_admission import (
    assess_model_admission,
    load_admission_config,
)
from rootzone_mpc.experiments.two_layer_bridge_data import (
    generate_bridge_identification_data,
    load_protocol,
)
from rootzone_mpc.models.two_layer_plant import (
    TwoLayerPlantParameters,
    TwoLayerRootZonePlant,
)


ROOT = Path(__file__).resolve().parents[1]


def protocol():
    return load_protocol(ROOT / "configs/two_layer_closed_loop_protocol_v1.yaml")


def parameters():
    cfg = protocol()
    values = dict(cfg["plant_nominal"])
    values.update({
        "process_noise_sd_m3_m3": cfg["synthetic_noise_reference"]["process_noise_sd_m3_m3"],
        "measurement_noise_sd_m3_m3": cfg["synthetic_noise_reference"]["measurement_noise_sd_m3_m3"],
        "command_to_delivered_relative_sd": cfg["synthetic_noise_reference"]["command_to_delivered_relative_sd"],
    })
    return TwoLayerPlantParameters(**values)


def test_two_layer_plant_preserves_total_water_balance_without_clipping():
    plant = TwoLayerRootZonePlant(parameters(), 0.20, 0.21, 11)
    residuals = []
    for step in range(80):
        outcome = plant.step(1.0 if step < 10 else 0.0, 450.0, 1.1)
        residuals.append(outcome.water_balance_residual_mm)
    assert max(abs(value) for value in residuals) < 1e-12


def test_independent_plant_module_does_not_import_fitted_greybox_model():
    source = (ROOT / "src/rootzone_mpc/models/two_layer_plant.py").read_text(encoding="utf-8")
    assert "two_layer_greybox" not in source
    assert "two_layer_identification" not in source


def test_irrigation_command_and_delivered_water_are_distinct():
    plant = TwoLayerRootZonePlant(parameters(), 0.20, 0.21, 12)
    outcome = plant.step(1.0, 0.0, 0.5)
    assert outcome.irrigation_command_mm == 1.0
    assert 0.0 < outcome.irrigation_delivered_mm < 1.0


def test_bridge_data_are_deterministic_and_use_disjoint_roles():
    first, first_diagnostics = generate_bridge_identification_data(protocol())
    second, second_diagnostics = generate_bridge_identification_data(protocol())
    pd.testing.assert_frame_equal(first, second)
    assert first_diagnostics == second_diagnostics
    role_by_cycle = first.groupby("cycle_id")["dataset_role"].nunique()
    assert (role_by_cycle == 1).all()
    assert first_diagnostics["cycle_counts"] == {"identification": 6, "validation": 3}
    assert first_diagnostics["plant_imports_fitted_model_parameters"] is False


def test_bridge_data_pass_structural_field_quality_gate():
    frame, diagnostics = generate_bridge_identification_data(protocol())
    quality = load_quality_config(ROOT / "configs/field_data_quality_v1.yaml")
    result = validate_field_data(frame, quality)
    assert result["status"] == "passed", result["errors"]
    assert diagnostics["maximum_absolute_water_balance_residual_mm"] < 1e-12
    assert np.isfinite(frame[["theta_10cm_m3_m3", "theta_25cm_m3_m3"]]).all().all()


def test_bridge_identification_data_pass_information_admission_before_fit():
    frame, _ = generate_bridge_identification_data(protocol())
    quality = load_quality_config(ROOT / "configs/field_data_quality_v1.yaml")
    admission = load_admission_config(ROOT / "configs/field_model_admission_v1.yaml")
    result = assess_model_admission(frame, quality, admission)
    assert result["model_admission_decision"] == "provisional_ready_for_identification"
    assert result["information_assessment"]["identification_cycle_count"] == 6
    assert result["information_assessment"]["usable_transition_count"] == 576
    assert result["information_assessment"]["minimum_information_singular_value_snr"] > 28.0
    assert result["information_assessment"]["combined_predicted_normalized_se_rms"] < 0.018
