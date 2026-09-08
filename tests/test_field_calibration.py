from pathlib import Path
import json

from scripts.generate_calibration_examples import flow_example, moisture_example
from rootzone_mpc.data.calibration_quality import (
    evaluate_flow_calibration,
    evaluate_moisture_calibration,
    load_calibration_config,
    run_calibration_assessment,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs/field_calibration_quality_v1.yaml"


def config() -> dict:
    return load_calibration_config(CONFIG_PATH)


def test_synthetic_moisture_calibration_passes_frozen_checks():
    result = evaluate_moisture_calibration(moisture_example(), config()["moisture"])
    assert result["status"] == "passed"
    assert len(result["sensors"]) == 2
    assert all(result["checks"].values())


def test_missing_moisture_level_fails_coverage_gate():
    frame = moisture_example()
    frame = frame[frame["moisture_level_id"] != "L05"]
    result = evaluate_moisture_calibration(frame, config()["moisture"])
    assert result["status"] == "failed"
    assert result["checks"]["minimum_levels_met"] is False


def test_single_moisture_level_returns_failure_without_fit_error():
    frame = moisture_example()
    frame = frame[frame["moisture_level_id"] == "L01"]
    result = evaluate_moisture_calibration(frame, config()["moisture"])
    assert result["status"] == "failed"
    assert result["checks"]["minimum_levels_met"] is False
    json.dumps(result, allow_nan=False)


def test_naive_calibration_timestamps_are_rejected():
    frame = moisture_example()
    frame["timestamp_utc"] = frame["timestamp_utc"].str.replace("Z", "", regex=False)
    result = evaluate_moisture_calibration(frame, config()["moisture"])
    assert result["status"] == "failed"
    assert result["checks"]["timestamps_parse_and_include_timezone"] is False


def test_synthetic_flow_calibration_passes_frozen_checks():
    result = evaluate_flow_calibration(flow_example(), config()["flow"])
    assert result["status"] == "passed"
    assert len(result["devices"]) == 1
    assert all(result["checks"].values())


def test_large_flow_error_fails_corrected_error_gate():
    frame = flow_example()
    frame.loc[0, "device_reported_volume_l"] *= 1.5
    result = evaluate_flow_calibration(frame, config()["flow"])
    assert result["status"] == "failed"
    assert result["checks"]["corrected_relative_error_within_limit"] is False


def test_calibration_handoff_never_invents_process_noise(tmp_path):
    moisture_path = tmp_path / "moisture.csv"
    flow_path = tmp_path / "flow.csv"
    moisture_example().to_csv(moisture_path, index=False)
    flow_example().to_csv(flow_path, index=False)
    result = run_calibration_assessment(
        moisture_path, flow_path, CONFIG_PATH, ROOT
    )
    handoff = result["model_admission_handoff"]
    assert result["status"] == "passed"
    assert handoff["measurement_noise_sd_m3_m3"] is not None
    assert handoff["process_noise_sd_m3_m3"] is None
    assert handoff["field_information_threshold_ready"] is False
