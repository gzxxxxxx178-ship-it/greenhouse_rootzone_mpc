from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE task_runs (
  task_id TEXT PRIMARY KEY, anomaly_scenario_id TEXT NOT NULL, method TEXT NOT NULL,
  status TEXT NOT NULL, total_steps INTEGER NOT NULL,
  controller_sha256 TEXT NOT NULL, supervision_sha256 TEXT NOT NULL
);
CREATE TABLE commands (
  task_id TEXT NOT NULL, step INTEGER NOT NULL, mode TEXT NOT NULL,
  requested_mm REAL NOT NULL, execution_status TEXT NOT NULL,
  PRIMARY KEY (task_id, step), FOREIGN KEY (task_id) REFERENCES task_runs(task_id)
);
CREATE TABLE feedback (
  task_id TEXT NOT NULL, step INTEGER NOT NULL, measured_theta REAL NOT NULL,
  delivered_mm REAL NOT NULL, feedback_consistent INTEGER NOT NULL,
  PRIMARY KEY (task_id, step),
  FOREIGN KEY (task_id, step) REFERENCES commands(task_id, step)
);
CREATE TABLE interlocks (
  task_id TEXT NOT NULL, step INTEGER NOT NULL, interlock_type TEXT NOT NULL,
  reason TEXT NOT NULL, PRIMARY KEY (task_id, step),
  FOREIGN KEY (task_id, step) REFERENCES commands(task_id, step)
);
CREATE TABLE mode_transitions (
  task_id TEXT NOT NULL, step INTEGER NOT NULL, from_mode TEXT NOT NULL,
  to_mode TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY (task_id, step),
  FOREIGN KEY (task_id, step) REFERENCES commands(task_id, step)
);
"""


class AuditStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.executescript(SCHEMA)

    def record_trace(
        self,
        task_id: str,
        trace: pd.DataFrame,
        controller_sha256: str,
        supervision_sha256: str,
    ) -> None:
        first = trace.iloc[0]
        with self.connection:
            self.connection.execute(
                "INSERT INTO task_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, first.anomaly_scenario_id, first.method, "running", len(trace),
                 controller_sha256, supervision_sha256),
            )
            previous_mode = None
            for row in trace.itertuples(index=False):
                execution_status = "blocked" if row.mode == "safe_pause" else "executed"
                self.connection.execute(
                    "INSERT INTO commands VALUES (?, ?, ?, ?, ?)",
                    (task_id, row.step, row.mode, row.command_mm, execution_status),
                )
                self.connection.execute(
                    "INSERT INTO feedback VALUES (?, ?, ?, ?, ?)",
                    (task_id, row.step, row.theta_true, row.delivered_command_mm,
                     int(row.feedback_consistent)),
                )
                if row.mode != "mpc":
                    self.connection.execute(
                        "INSERT INTO interlocks VALUES (?, ?, ?, ?)",
                        (task_id, row.step, row.mode, row.reason),
                    )
                if previous_mode is not None and row.mode != previous_mode:
                    self.connection.execute(
                        "INSERT INTO mode_transitions VALUES (?, ?, ?, ?, ?)",
                        (task_id, row.step, previous_mode, row.mode, row.reason),
                    )
                previous_mode = row.mode
            self.connection.execute(
                "UPDATE task_runs SET status='completed' WHERE task_id=?", (task_id,)
            )

    def scalar(self, query: str) -> int:
        return int(self.connection.execute(query).fetchone()[0])

    def table(self, name: str) -> pd.DataFrame:
        order = "task_id" if name == "task_runs" else "task_id, step"
        return pd.read_sql_query(f"SELECT * FROM {name} ORDER BY {order}", self.connection)

    def close(self) -> None:
        self.connection.close()
