from pathlib import Path

import pandas as pd

from rootzone_mpc.platform import AuditStore


def test_audit_store_links_task_command_feedback_and_transition(tmp_path: Path):
    trace = pd.DataFrame([
        {"anomaly_scenario_id": "A1", "method": "trustworthy_supervision", "step": 0,
         "mode": "mpc", "command_mm": 2.0, "theta_true": 0.21,
         "delivered_command_mm": 2.0, "feedback_consistent": True, "reason": "nominal"},
        {"anomaly_scenario_id": "A1", "method": "trustworthy_supervision", "step": 1,
         "mode": "safe_pause", "command_mm": 0.0, "theta_true": 0.20,
         "delivered_command_mm": 0.0, "feedback_consistent": False, "reason": "feedback"},
    ])
    store = AuditStore(tmp_path / "audit.sqlite")
    store.record_trace("T1", trace, "controller", "supervision")
    assert store.scalar("SELECT COUNT(*) FROM commands") == 2
    assert store.scalar("SELECT COUNT(*) FROM feedback") == 2
    assert store.scalar("SELECT COUNT(*) FROM interlocks") == 1
    assert store.scalar("SELECT COUNT(*) FROM mode_transitions") == 1
    assert store.scalar("SELECT COUNT(*) FROM task_runs WHERE status='completed'") == 1
    store.close()
