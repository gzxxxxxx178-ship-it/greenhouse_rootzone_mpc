from pathlib import Path

import pandas as pd

from rootzone_mpc.data.field_quality import load_quality_config, validate_field_data


ROOT = Path(__file__).resolve().parents[1]


def example_frame() -> pd.DataFrame:
    path = ROOT / "data/examples/synthetic_field_observations_v1.csv"
    return pd.read_csv(path)


def config() -> dict:
    return load_quality_config(ROOT / "configs/field_data_quality_v1.yaml")


def test_complete_example_passes_all_quality_gates():
    result = validate_field_data(example_frame(), config())
    assert result["status"] == "passed"
    assert result["cycle_counts"] == {"identification": 3, "validation": 2}
    assert len(result["cycle_summary"]) == 5
    assert all(result["checks"].values())


def test_identification_data_cannot_replace_independent_validation_cycles():
    frame = example_frame()
    frame["dataset_role"] = "identification"
    result = validate_field_data(frame, config())
    assert result["status"] == "failed"
    assert result["checks"]["minimum_validation_cycles"] is False


def test_flow_meter_disagreement_fails_water_accounting_gate():
    frame = example_frame()
    frame.loc[frame["flow_l_min"] > 0, "flow_l_min"] = 0.1
    result = validate_field_data(frame, config())
    assert result["status"] == "failed"
    assert result["checks"]["flow_matches_delivered_water"] is False


def test_naive_timestamps_are_rejected():
    frame = example_frame()
    frame["timestamp_utc"] = frame["timestamp_utc"].str.replace("Z", "", regex=False)
    result = validate_field_data(frame, config())
    assert result["status"] == "failed"
    assert result["checks"]["timestamps_parse_and_include_timezone"] is False


def test_sampling_slower_than_fifteen_minutes_is_rejected():
    frame = example_frame()
    frame["sample_interval_minutes"] = 30
    result = validate_field_data(frame, config())
    assert result["status"] == "failed"
    assert result["checks"]["sample_interval_within_limit"] is False
