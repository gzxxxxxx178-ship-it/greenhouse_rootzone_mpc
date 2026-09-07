from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import yaml

from rootzone_mpc.experiments.supervision_confirmation import _run_method
from rootzone_mpc.platform import AuditStore


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_platform_sil(project_root: Path) -> tuple[Path, Path]:
    paths = {
        "sil": project_root / "configs/platform_sil_v1.yaml",
        "anomaly": project_root / "configs/anomaly_design_v1.yaml",
        "confirmation": project_root / "configs/supervision_confirmation_v1.yaml",
        "tuning": project_root / "configs/tuning_design_v1.yaml",
        "controller": project_root / "configs/frozen_controller_v1.yaml",
        "supervision": project_root / "configs/frozen_supervision_v1.yaml",
    }
    loaded = {key: yaml.safe_load(path.read_text(encoding="utf-8")) for key, path in paths.items()}
    cfg = loaded["sil"]["platform_sil"]
    scenarios = pd.read_csv(project_root / "data/processed/scenario_manifest_v1.csv").set_index("scenario_id")
    anomalies = pd.read_csv(project_root / "data/processed/anomaly_manifest_v1.csv")
    selected = (
        anomalies[
            (anomalies["split"] == cfg["split"])
            & anomalies["anomaly_type"].isin(cfg["anomaly_types"])
        ]
        .sort_values(["anomaly_type", "anomaly_scenario_id"])
        .groupby("anomaly_type")
        .head(int(cfg["scenarios_per_type"]))
    )
    if set(selected["anomaly_type"]) != set(cfg["anomaly_types"]):
        raise ValueError("Platform SIL selection does not cover configured anomaly types")

    db_path = project_root / "outputs/runs/platform_sil_v1.sqlite"
    store = AuditStore(db_path)
    controller_sha = _sha(paths["controller"])
    supervision_sha = _sha(paths["supervision"])
    trace_transition_total = 0
    try:
        for sequence, (_, anomaly) in enumerate(selected.iterrows(), start=1):
            scenario = scenarios.loc[anomaly["base_scenario_id"]].copy()
            scenario["scenario_id"] = anomaly["base_scenario_id"]
            _, trace = _run_method(
                scenario, anomaly, cfg["method"], loaded["tuning"],
                loaded["controller"], loaded["supervision"], loaded["anomaly"],
                loaded["confirmation"],
            )
            trace_transition_total += int((trace["mode"] != trace["mode"].shift()).sum() - 1)
            store.record_trace(f"SIL-{sequence:03d}", trace, controller_sha, supervision_sha)

        task_count = store.scalar("SELECT COUNT(*) FROM task_runs")
        command_count = store.scalar("SELECT COUNT(*) FROM commands")
        feedback_count = store.scalar("SELECT COUNT(*) FROM feedback")
        interlock_count = store.scalar("SELECT COUNT(*) FROM interlocks")
        transition_count = store.scalar("SELECT COUNT(*) FROM mode_transitions")
        total_steps = store.scalar("SELECT SUM(total_steps) FROM task_runs")
        invariants = {
            "one_command_per_step": command_count == total_steps,
            "one_feedback_per_command": feedback_count == command_count,
            "no_orphan_records": store.scalar(
                "SELECT COUNT(*) FROM feedback f LEFT JOIN commands c USING(task_id, step) WHERE c.task_id IS NULL"
            ) == 0,
            "safe_pause_commands_are_zero": store.scalar(
                "SELECT COUNT(*) FROM commands WHERE mode='safe_pause' AND requested_mm != 0"
            ) == 0,
            "mode_transitions_match_trace": transition_count == trace_transition_total,
            "versions_bound_to_every_task": store.scalar(
                "SELECT COUNT(*) FROM task_runs WHERE controller_sha256='' OR supervision_sha256=''"
            ) == 0,
        }
        if not all(invariants.values()):
            raise AssertionError(f"Platform SIL invariant failed: {invariants}")
        event_path = project_root / "outputs/tables/platform_sil_task_runs.csv"
        store.table("task_runs").to_csv(event_path, index=False)
    finally:
        store.close()

    result_path = project_root / "data/processed/platform_sil_v1.json"
    result = {
        "name": cfg["name"], "status": "passed", "task_count": task_count,
        "total_steps": total_steps, "command_count": command_count,
        "feedback_count": feedback_count, "interlock_count": interlock_count,
        "mode_transition_count": transition_count, "invariants": invariants,
        "selected_anomaly_scenarios": selected["anomaly_scenario_id"].tolist(),
        "version_hashes": {"controller": controller_sha, "supervision": supervision_sha},
        "evidence_boundary": cfg["evidence_boundary"],
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return db_path, result_path
