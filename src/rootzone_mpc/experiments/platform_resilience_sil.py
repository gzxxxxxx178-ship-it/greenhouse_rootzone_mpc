from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import yaml

from rootzone_mpc.platform import ReliableCommandChannel


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _publish(channel: ReliableCommandChannel, task_id: str, count: int, timeout: int) -> None:
    for sequence in range(1, count + 1):
        channel.publish_command(
            task_id, sequence, f"{task_id}-CMD-{sequence:03d}",
            float(sequence % 2) * 2.0, issued_tick=0, timeout_ticks=timeout,
        )


def run_platform_resilience_sil(project_root: Path) -> tuple[Path, ...]:
    config_path = project_root / "configs/platform_resilience_sil_v1.yaml"
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))["platform_resilience_sil"]
    algorithm_path = project_root / cfg["algorithm_config"]
    parameter_path = project_root / cfg["parameter_config"]
    algorithm_sha, parameter_sha = _sha(algorithm_path), _sha(parameter_path)
    timeout = int(cfg["command_timeout_ticks"])
    db_path = project_root / "outputs/runs/platform_resilience_sil_v1.sqlite"
    channel = ReliableCommandChannel(db_path, reset=True)

    try:
        task = "RES-DUP"
        channel.register_task(task, algorithm_sha, parameter_sha)
        _publish(channel, task, 3, timeout)
        assert not channel.publish_command(task, 3, f"{task}-CMD-003", 2.0, 0, timeout)
        channel.receive_feedback("DUP-FB-001", task, 1, 2.0, 1)
        channel.receive_feedback("DUP-FB-001", task, 1, 2.0, 2)
        channel.receive_feedback("DUP-FB-001-B", task, 1, 2.0, 2)
        channel.receive_feedback("DUP-FB-002", task, 2, 0.0, 2)
        channel.receive_feedback("DUP-FB-003", task, 3, 2.0, 3)
        channel.complete_task(task, 4)

        task = "RES-ORDER"
        channel.register_task(task, algorithm_sha, parameter_sha)
        _publish(channel, task, 4, timeout)
        channel.receive_feedback("ORDER-FB-002", task, 2, 0.0, 1)
        channel.receive_feedback("ORDER-FB-001", task, 1, 2.0, 2)
        channel.receive_feedback("ORDER-FB-004", task, 4, 0.0, 2)
        channel.receive_feedback("ORDER-FB-003", task, 3, 2.0, 3)
        channel.complete_task(task, 4)

        task = "RES-TIMEOUT"
        channel.register_task(task, algorithm_sha, parameter_sha)
        _publish(channel, task, 3, timeout)
        channel.receive_feedback("TIME-FB-002", task, 2, 0.0, 4)
        channel.receive_feedback("TIME-FB-003", task, 3, 2.0, 4)
        channel.expire_timeouts(6)
        channel.receive_feedback("TIME-FB-001-LATE", task, 1, 2.0, 7)
        channel.complete_task(task, 8)

        channel.close()
        crash_code = """
import os
import sys
from pathlib import Path
from rootzone_mpc.platform import ReliableCommandChannel

database, algorithm_sha, parameter_sha, timeout = sys.argv[1:]
channel = ReliableCommandChannel(Path(database), reset=False)
task = "RES-CRASH"
channel.register_task(task, algorithm_sha, parameter_sha)
for sequence in range(1, 5):
    channel.publish_command(
        task, sequence, f"{task}-CMD-{sequence:03d}", float(sequence % 2) * 2.0,
        issued_tick=0, timeout_ticks=int(timeout),
    )
channel.receive_feedback("CRASH-FB-001", task, 1, 2.0, 1)
channel.receive_feedback("CRASH-FB-003", task, 3, 2.0, 2)
os._exit(23)
"""
        crashed = subprocess.run(
            [sys.executable, "-c", crash_code, str(db_path), algorithm_sha, parameter_sha, str(timeout)],
            check=False,
        )
        if crashed.returncode != 23:
            raise AssertionError(f"Crash worker returned {crashed.returncode}, expected 23")
        channel = ReliableCommandChannel(db_path, reset=False)
        task = "RES-CRASH"
        pre_crash = {
            "pending": channel.scalar(
                "SELECT COUNT(*) FROM channel_commands WHERE task_id=? AND status='pending'", (task,)
            ),
            "buffered": channel.scalar(
                "SELECT COUNT(*) FROM feedback_inbox WHERE task_id=? AND disposition='buffered_out_of_order'",
                (task,),
            ),
        }
        recovered = channel.recover(3)
        channel.receive_feedback("CRASH-FB-002", task, 2, 0.0, 3)
        channel.receive_feedback("CRASH-FB-004", task, 4, 0.0, 4)
        channel.complete_task(task, 5)

        commands = channel.table("channel_commands")
        inbox = channel.table("feedback_inbox")
        applied = channel.table("applied_feedback")
        events = channel.table("channel_events")
        tasks = channel.table("channel_tasks")

        order_ok = all(
            group["sequence"].tolist() == sorted(group["sequence"].tolist())
            for _, group in applied.groupby("task_id", sort=False)
        )
        timeout_later = applied[applied["task_id"] == "RES-TIMEOUT"]["sequence"].tolist()
        invariants = {
            "logical_feedback_applied_at_most_once": not applied.duplicated(["task_id", "sequence"]).any(),
            "applied_feedback_is_sequence_ordered": order_ok,
            "late_feedback_never_applied": not set(
                inbox.loc[inbox["disposition"] == "late_rejected", "message_id"]
            ) & set(applied["message_id"]),
            "timeout_gap_does_not_deadlock_later_feedback": timeout_later == [2, 3],
            "restart_preserves_pending_state": pre_crash == {"pending": 3, "buffered": 1}
            and recovered == {"tasks": 1, "pending_commands": 3, "buffered_feedback": 1},
            "every_task_has_version_hashes": not (
                (tasks["algorithm_sha256"] == "") | (tasks["parameter_sha256"] == "")
            ).any(),
            "no_orphan_applied_feedback": len(applied.merge(
                commands[["task_id", "sequence"]], on=["task_id", "sequence"], how="left", indicator=True
            ).query("_merge != 'both'")) == 0,
        }
        if set(invariants) != set(cfg["required_invariants"]) or not all(invariants.values()):
            raise AssertionError(f"Platform resilience invariant failed: {invariants}")

        table_dir = project_root / "outputs/tables"
        table_dir.mkdir(parents=True, exist_ok=True)
        event_path = table_dir / "platform_resilience_events_v1.csv"
        applied_path = table_dir / "platform_resilience_applied_feedback_v1.csv"
        events.to_csv(event_path, index=False)
        applied.to_csv(applied_path, index=False)
    finally:
        channel.close()

    result_path = project_root / "data/processed/platform_resilience_sil_v1.json"
    event_counts = events.groupby("event_type").size().sort_index().to_dict()
    delivery_attempt_count = sum(
        event_counts.get(name, 0)
        for name in (
            "ready", "buffered_out_of_order", "duplicate_message_rejected",
            "duplicate_logical_feedback_rejected", "late_feedback_rejected",
        )
    )
    result = {
        "name": cfg["name"],
        "status": "passed",
        "scenario_count": len(cfg["scenarios"]),
        "task_count": int(len(tasks)),
        "command_count": int(len(commands)),
        "feedback_delivery_attempt_count": int(delivery_attempt_count),
        "persisted_feedback_message_count": int(len(inbox)),
        "applied_feedback_count": int(len(applied)),
        "timed_out_command_count": int((commands["status"] == "timed_out").sum()),
        "event_counts": {key: int(value) for key, value in event_counts.items()},
        "invariants": invariants,
        "version_hashes": {"algorithm": algorithm_sha, "parameters": parameter_sha},
        "evidence_boundary": cfg["evidence_boundary"],
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return db_path, event_path, applied_path, result_path
