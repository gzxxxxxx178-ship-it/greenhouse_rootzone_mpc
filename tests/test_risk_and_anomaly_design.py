from pathlib import Path

import pandas as pd
import pytest

from rootzone_mpc.design import build_anomaly_manifest
from rootzone_mpc.supervision import OnlineRiskMonitor, RiskMonitorConfig, RiskScale


def test_risk_scale_clips_to_unit_interval():
    scale = RiskScale(0.1, 0.3)
    assert scale.normalize(0.0) == 0.0
    assert scale.normalize(0.2) == pytest.approx(0.5)
    assert scale.normalize(0.4) == 1.0


def test_missing_observation_sets_invalid_signal():
    monitor = OnlineRiskMonitor(
        RiskMonitorConfig(
            observation_rate_scale=RiskScale(0.001, 0.01),
            model_residual_scale=RiskScale(0.001, 0.01),
            execution_error_scale=RiskScale(0.01, 0.10),
            residual_ewma_alpha=0.2,
            maximum_observation_age_steps=2,
            valid_theta_min=0.08,
            valid_theta_max=0.45,
        )
    )
    result = monitor.update(None, 0.2, 3, 0.0, 0.0, True, True, True)
    assert not result.observation_valid
    assert result.observation_risk == 1.0


def test_anomaly_manifest_is_balanced_and_split_safe():
    root = Path(__file__).resolve().parents[1]
    manifest_path, _ = build_anomaly_manifest(root)
    frame = pd.read_csv(manifest_path)
    counts = frame.groupby(["split", "anomaly_type"]).size()
    assert counts.loc["development"].nunique() == 1
    assert counts.loc["locked_evaluation"].nunique() == 1
    assert set(counts.loc["development"]) == {4}
    assert set(counts.loc["locked_evaluation"]) == {10}
