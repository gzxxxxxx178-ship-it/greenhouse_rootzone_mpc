from pathlib import Path

import pandas as pd
import yaml

from rootzone_mpc.experiments.supervision_v2_confirmation import _classify


def test_v2_confirmation_gate_requires_all_registered_conditions():
    root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((root / "configs/supervision_v2_confirmation.yaml").read_text())
    kinds = ["normal"] + cfg["confirmation"]["target_v1_failure_types"] + [
        "drainage_shift", "forecast_underprediction", "root_depth_shift",
        "sensor_spike", "solver_failure",
    ]
    rows = []
    for index, kind in enumerate(kinds):
        for method, irrigation, non_mpc in [
            ("base_mpc", 100.0, 0.0),
            ("trustworthy_supervision", 102.0, 0.05),
            ("trustworthy_supervision_v2", 110.0, 0.08),
        ]:
            rows.append({
                "anomaly_scenario_id": f"A{index}", "anomaly_type": kind,
                "method": method, "irrigation_command_total_mm": irrigation,
                "non_mpc_fraction": non_mpc, "safety_violation_steps": 0,
            })
    metrics = pd.DataFrame(rows)
    v2_v1 = pd.DataFrame({
        "anomaly_type": kinds,
        "delta_event_deficit_integral": [-0.1 if kind in cfg["confirmation"]["target_v1_failure_types"] else 0.0 for kind in kinds],
    })
    v2_base = pd.DataFrame({
        "anomaly_type": kinds, "delta_event_deficit_integral": [0.0] * len(kinds)
    })
    label, diagnostics = _classify(metrics, v2_base, v2_v1, cfg)
    assert label == "V2_INDEPENDENT_CONFIRMATION_SUPPORTED"
    assert diagnostics["success_gate_passed"]
