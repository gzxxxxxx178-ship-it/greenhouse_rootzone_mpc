from pathlib import Path

import pandas as pd
import pytest
import yaml

from rootzone_mpc.experiments.development import run_development_scenario


def test_tuning_rejects_locked_evaluation_scenario():
    root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((root / "configs" / "tuning_design_v1.yaml").read_text())
    row = pd.read_csv(root / "data" / "processed" / "scenario_manifest_v1.csv").query(
        "split == 'locked_evaluation'"
    ).iloc[0]
    parameters = {
        **cfg["rule_fixed"],
        "start_threshold": 0.205,
        "stop_threshold": 0.235,
        "pulse_mm": 4.0,
    }
    with pytest.raises(ValueError, match="development"):
        run_development_scenario(row, "rule", parameters, cfg)
