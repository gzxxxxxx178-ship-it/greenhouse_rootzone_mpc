from pathlib import Path

import pandas as pd

from rootzone_mpc.data.field_quality import load_quality_config
from rootzone_mpc.data.model_admission import (
    assess_model_admission,
    field_identification_design,
    load_admission_config,
)


ROOT = Path(__file__).resolve().parents[1]


def example_frame() -> pd.DataFrame:
    return pd.read_csv(ROOT / "data/examples/synthetic_field_observations_v1.csv")


def configs() -> tuple[dict, dict]:
    quality = load_quality_config(ROOT / "configs/field_data_quality_v1.yaml")
    admission = load_admission_config(ROOT / "configs/field_model_admission_v1.yaml")
    return quality, admission


def test_weak_example_passes_quality_but_requests_more_excitation():
    quality, admission = configs()
    result = assess_model_admission(example_frame(), quality, admission)
    assert result["quality_status"] == "passed"
    assert result["model_admission_decision"] == "collect_more_excitation"
    assert result["admission_checks"]["information_strength_sufficient"] is False
    assert result["admission_checks"]["predicted_uncertainty_sufficient"] is False


def test_validation_cycles_are_excluded_from_information_matrix():
    original = example_frame()
    changed = original.copy()
    mask = changed["dataset_role"] == "validation"
    changed.loc[mask, "theta_10cm_m3_m3"] = 0.60
    changed.loc[mask, "irrigation_delivered_mm"] = 20.0
    first, first_diagnostics = field_identification_design(original)
    second, second_diagnostics = field_identification_design(changed)
    assert first_diagnostics == second_diagnostics
    assert (first == second).all()


def test_example_has_three_cycles_and_141_usable_transitions():
    design, diagnostics = field_identification_design(example_frame())
    assert design.shape == (141, 5)
    assert diagnostics["identification_cycle_count"] == 3
    assert diagnostics["design_rank"] == 5
    assert diagnostics["input_interval_alignment"] == "row_ending_at_next_state_timestamp"


def test_transition_uses_input_reported_at_following_state_timestamp():
    frame = example_frame()
    mask = frame["dataset_role"] == "identification"
    frame.loc[mask, "irrigation_delivered_mm"] = 0.0
    first_cycle = frame.index[mask & frame["cycle_id"].eq("C01")]
    frame.loc[first_cycle[1], "irrigation_delivered_mm"] = 3.25
    design, _ = field_identification_design(frame)
    assert design[0, 1] == 3.25
    assert design[1, 1] == 0.0


def test_failed_base_quality_blocks_information_assessment():
    quality, admission = configs()
    frame = example_frame().drop(columns=["flow_l_min"])
    result = assess_model_admission(frame, quality, admission)
    assert result["model_admission_decision"] == "blocked_data_quality"
    assert result["information_assessment"] is None
