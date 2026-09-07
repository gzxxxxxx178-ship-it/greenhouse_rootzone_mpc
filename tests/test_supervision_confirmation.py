from pathlib import Path

import pandas as pd
import yaml

from rootzone_mpc.experiments.supervision_confirmation import (
    _active,
    _observed_value,
    _recovery_delay_steps,
)


def anomaly(kind: str = "sensor_bias") -> pd.Series:
    return pd.Series(
        {
            "anomaly_type": kind,
            "start_step": 10,
            "end_step": 20,
        }
    )


def magnitudes() -> dict:
    root = Path(__file__).resolve().parents[1]
    return yaml.safe_load((root / "configs" / "anomaly_design_v1.yaml").read_text())[
        "magnitudes"
    ]


def test_anomaly_interval_is_half_open():
    item = anomaly()
    assert not _active(9, item)
    assert _active(10, item)
    assert _active(19, item)
    assert not _active(20, item)


def test_missing_and_bias_observation_injection():
    assert _observed_value(0.2, 12, anomaly("sensor_missing"), magnitudes()) is None
    biased = _observed_value(0.2, 12, anomaly("sensor_bias"), magnitudes())
    assert biased == 0.225
    assert _observed_value(0.2, 9, anomaly("sensor_bias"), magnitudes()) == 0.2


def test_recovery_delay_uses_first_mpc_step_after_event():
    trace = pd.DataFrame(
        {
            "step": [18, 19, 20, 21, 22],
            "mode": ["safe_pause", "rule_fallback", "rule_fallback", "mpc", "mpc"],
        }
    )
    assert _recovery_delay_steps(trace, anomaly("solver_failure")) == 1
    assert _recovery_delay_steps(trace, anomaly("normal")) is None
