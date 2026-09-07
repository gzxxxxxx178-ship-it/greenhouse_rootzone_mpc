from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS channel_tasks (
  task_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  next_feedback_sequence INTEGER NOT NULL,
  algorithm_sha256 TEXT NOT NULL,
  parameter_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS channel_commands (
  task_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  command_id TEXT NOT NULL UNIQUE,
  requested_mm REAL NOT NULL,
  issued_tick INTEGER NOT NULL,
  deadline_tick INTEGER NOT NULL,
  status TEXT NOT NULL,
  PRIMARY KEY (task_id, sequence),
  FOREIGN KEY (task_id) REFERENCES channel_tasks(task_id)
);
CREATE TABLE IF NOT EXISTS feedback_inbox (
  message_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  delivered_mm REAL NOT NULL,
  received_tick INTEGER NOT NULL,
  disposition TEXT NOT NULL,
  UNIQUE (task_id, sequence),
  FOREIGN KEY (task_id, sequence) REFERENCES channel_commands(task_id, sequence)
);
CREATE TABLE IF NOT EXISTS applied_feedback (
  task_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  message_id TEXT NOT NULL UNIQUE,
  delivered_mm REAL NOT NULL,
  applied_tick INTEGER NOT NULL,
  PRIMARY KEY (task_id, sequence),
  FOREIGN KEY (message_id) REFERENCES feedback_inbox(message_id),
  FOREIGN KEY (task_id, sequence) REFERENCES channel_commands(task_id, sequence)
);
CREATE TABLE IF NOT EXISTS channel_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  sequence INTEGER,
  event_type TEXT NOT NULL,
  event_tick INTEGER NOT NULL,
  detail TEXT NOT NULL
);
"""


class ReliableCommandChannel:
    """Persistent ordered command-feedback channel for deterministic SIL tests."""

    def __init__(self, path: Path, *, reset: bool = False):
        path.parent.mkdir(parents=True, exist_ok=True)
        if reset:
            for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
                candidate.unlink(missing_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.executescript(SCHEMA)

    def _event(
        self, task_id: str, sequence: int | None, event_type: str, tick: int, detail: str
    ) -> None:
        self.connection.execute(
            "INSERT INTO channel_events(task_id, sequence, event_type, event_tick, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, sequence, event_type, tick, detail),
        )

    def register_task(
        self, task_id: str, algorithm_sha256: str, parameter_sha256: str, tick: int = 0
    ) -> bool:
        with self.connection:
            row = self.connection.execute(
                "SELECT algorithm_sha256, parameter_sha256 FROM channel_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is not None:
                if row != (algorithm_sha256, parameter_sha256):
                    raise ValueError("Task version hashes cannot change after registration")
                self._event(task_id, None, "duplicate_task_registration", tick, "unchanged")
                return False
            self.connection.execute(
                "INSERT INTO channel_tasks VALUES (?, 'running', 1, ?, ?)",
                (task_id, algorithm_sha256, parameter_sha256),
            )
            self._event(task_id, None, "task_registered", tick, "next_sequence=1")
        return True

    def publish_command(
        self,
        task_id: str,
        sequence: int,
        command_id: str,
        requested_mm: float,
        issued_tick: int,
        timeout_ticks: int,
    ) -> bool:
        if sequence < 1 or timeout_ticks < 1 or requested_mm < 0:
            raise ValueError("Invalid command envelope")
        with self.connection:
            existing = self.connection.execute(
                "SELECT task_id, sequence, requested_mm, issued_tick, deadline_tick "
                "FROM channel_commands WHERE command_id=?",
                (command_id,),
            ).fetchone()
            expected = (task_id, sequence, float(requested_mm), issued_tick, issued_tick + timeout_ticks)
            if existing is not None:
                if existing != expected:
                    raise ValueError("A command_id cannot be reused with different content")
                self._event(task_id, sequence, "duplicate_command_publish", issued_tick, command_id)
                return False
            maximum = self.connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM channel_commands WHERE task_id=?",
                (task_id,),
            ).fetchone()[0]
            if sequence != maximum + 1:
                raise ValueError(f"Command sequence must be contiguous; expected {maximum + 1}")
            self.connection.execute(
                "INSERT INTO channel_commands VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (task_id, sequence, command_id, float(requested_mm), issued_tick,
                 issued_tick + timeout_ticks),
            )
            self._event(task_id, sequence, "command_published", issued_tick, command_id)
        return True

    def receive_feedback(
        self,
        message_id: str,
        task_id: str,
        sequence: int,
        delivered_mm: float,
        received_tick: int,
    ) -> str:
        with self.connection:
            duplicate_message = self.connection.execute(
                "SELECT 1 FROM feedback_inbox WHERE message_id=?", (message_id,)
            ).fetchone()
            if duplicate_message:
                self._event(task_id, sequence, "duplicate_message_rejected", received_tick, message_id)
                return "duplicate_message"
            command = self.connection.execute(
                "SELECT deadline_tick, status FROM channel_commands WHERE task_id=? AND sequence=?",
                (task_id, sequence),
            ).fetchone()
            if command is None:
                self._event(task_id, sequence, "unknown_feedback_rejected", received_tick, message_id)
                return "unknown_command"
            logical_duplicate = self.connection.execute(
                "SELECT 1 FROM feedback_inbox WHERE task_id=? AND sequence=?",
                (task_id, sequence),
            ).fetchone()
            if logical_duplicate:
                self._event(task_id, sequence, "duplicate_logical_feedback_rejected", received_tick, message_id)
                return "duplicate_logical"
            deadline_tick, status = command
            if status == "timed_out" or received_tick > deadline_tick:
                if status == "pending":
                    self.connection.execute(
                        "UPDATE channel_commands SET status='timed_out' WHERE task_id=? AND sequence=?",
                        (task_id, sequence),
                    )
                self.connection.execute(
                    "INSERT INTO feedback_inbox VALUES (?, ?, ?, ?, ?, 'late_rejected')",
                    (message_id, task_id, sequence, delivered_mm, received_tick),
                )
                self._event(task_id, sequence, "late_feedback_rejected", received_tick, message_id)
                self._advance(task_id, received_tick)
                return "late_rejected"

            expected_sequence = self.connection.execute(
                "SELECT next_feedback_sequence FROM channel_tasks WHERE task_id=?", (task_id,)
            ).fetchone()[0]
            disposition = "ready" if sequence == expected_sequence else "buffered_out_of_order"
            self.connection.execute(
                "INSERT INTO feedback_inbox VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, task_id, sequence, delivered_mm, received_tick, disposition),
            )
            self._event(task_id, sequence, disposition, received_tick, message_id)
            self._advance(task_id, received_tick)
            return disposition

    def expire_timeouts(self, current_tick: int) -> int:
        with self.connection:
            rows = self.connection.execute(
                "SELECT task_id, sequence FROM channel_commands "
                "WHERE status='pending' AND deadline_tick < ? "
                "AND NOT EXISTS (SELECT 1 FROM feedback_inbox f "
                "WHERE f.task_id=channel_commands.task_id AND f.sequence=channel_commands.sequence "
                "AND f.disposition!='late_rejected') "
                "ORDER BY task_id, sequence",
                (current_tick,),
            ).fetchall()
            for task_id, sequence in rows:
                self.connection.execute(
                    "UPDATE channel_commands SET status='timed_out' WHERE task_id=? AND sequence=?",
                    (task_id, sequence),
                )
                self._event(task_id, sequence, "command_timed_out", current_tick, "deadline_expired")
            for task_id in sorted({row[0] for row in rows}):
                self._advance(task_id, current_tick)
        return len(rows)

    def recover(self, current_tick: int) -> dict[str, int]:
        with self.connection:
            task_count = self.connection.execute(
                "SELECT COUNT(*) FROM channel_tasks WHERE status='running'"
            ).fetchone()[0]
            pending_count = self.connection.execute(
                "SELECT COUNT(*) FROM channel_commands c JOIN channel_tasks t USING(task_id) "
                "WHERE c.status='pending' AND t.status='running'"
            ).fetchone()[0]
            buffered_count = self.connection.execute(
                "SELECT COUNT(*) FROM feedback_inbox f JOIN channel_tasks t USING(task_id) "
                "WHERE f.disposition='buffered_out_of_order' AND t.status='running'"
            ).fetchone()[0]
            for (task_id,) in self.connection.execute(
                "SELECT task_id FROM channel_tasks WHERE status='running' ORDER BY task_id"
            ).fetchall():
                self._event(task_id, None, "process_recovered", current_tick, "persistent_state_loaded")
                self._advance(task_id, current_tick)
        return {"tasks": task_count, "pending_commands": pending_count, "buffered_feedback": buffered_count}

    def complete_task(self, task_id: str, tick: int) -> None:
        with self.connection:
            unresolved = self.connection.execute(
                "SELECT COUNT(*) FROM channel_commands WHERE task_id=? AND status='pending'", (task_id,)
            ).fetchone()[0]
            if unresolved:
                raise ValueError("Cannot complete a task with pending commands")
            self.connection.execute(
                "UPDATE channel_tasks SET status='completed' WHERE task_id=?", (task_id,)
            )
            self._event(task_id, None, "task_completed", tick, "no_pending_commands")

    def _advance(self, task_id: str, current_tick: int) -> None:
        while True:
            next_sequence = self.connection.execute(
                "SELECT next_feedback_sequence FROM channel_tasks WHERE task_id=?", (task_id,)
            ).fetchone()[0]
            command = self.connection.execute(
                "SELECT status FROM channel_commands WHERE task_id=? AND sequence=?",
                (task_id, next_sequence),
            ).fetchone()
            if command is None:
                return
            if command[0] == "timed_out":
                self.connection.execute(
                    "UPDATE channel_tasks SET next_feedback_sequence=? WHERE task_id=?",
                    (next_sequence + 1, task_id),
                )
                self._event(task_id, next_sequence, "timeout_gap_advanced", current_tick, "sequence_skipped")
                continue
            feedback = self.connection.execute(
                "SELECT message_id, delivered_mm FROM feedback_inbox "
                "WHERE task_id=? AND sequence=? AND disposition!='late_rejected'",
                (task_id, next_sequence),
            ).fetchone()
            if feedback is None:
                return
            message_id, delivered_mm = feedback
            self.connection.execute(
                "INSERT INTO applied_feedback VALUES (?, ?, ?, ?, ?)",
                (task_id, next_sequence, message_id, delivered_mm, current_tick),
            )
            self.connection.execute(
                "UPDATE channel_commands SET status='acknowledged' WHERE task_id=? AND sequence=?",
                (task_id, next_sequence),
            )
            self.connection.execute(
                "UPDATE feedback_inbox SET disposition='applied' WHERE message_id=?", (message_id,)
            )
            self.connection.execute(
                "UPDATE channel_tasks SET next_feedback_sequence=? WHERE task_id=?",
                (next_sequence + 1, task_id),
            )
            self._event(task_id, next_sequence, "feedback_applied", current_tick, message_id)

    def scalar(self, query: str, parameters: tuple = ()) -> int:
        return int(self.connection.execute(query, parameters).fetchone()[0])

    def table(self, name: str) -> pd.DataFrame:
        allowed = {
            "channel_tasks", "channel_commands", "feedback_inbox", "applied_feedback", "channel_events"
        }
        if name not in allowed:
            raise ValueError(f"Unknown table: {name}")
        return pd.read_sql_query(f"SELECT * FROM {name} ORDER BY rowid", self.connection)

    def close(self) -> None:
        self.connection.close()
