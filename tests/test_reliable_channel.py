from pathlib import Path

import pytest

from rootzone_mpc.platform import ReliableCommandChannel


def channel(tmp_path: Path) -> ReliableCommandChannel:
    store = ReliableCommandChannel(tmp_path / "channel.sqlite", reset=True)
    store.register_task("T1", "algorithm", "parameters")
    return store


def publish(store: ReliableCommandChannel, count: int = 3) -> None:
    for sequence in range(1, count + 1):
        store.publish_command("T1", sequence, f"C{sequence}", 2.0, 0, 5)


def test_duplicate_feedback_is_not_applied_twice(tmp_path: Path):
    store = channel(tmp_path)
    publish(store, 1)
    assert store.receive_feedback("F1", "T1", 1, 2.0, 1) == "ready"
    assert store.receive_feedback("F1", "T1", 1, 2.0, 2) == "duplicate_message"
    assert store.receive_feedback("F1B", "T1", 1, 2.0, 2) == "duplicate_logical"
    assert store.scalar("SELECT COUNT(*) FROM applied_feedback") == 1
    store.close()


def test_out_of_order_feedback_is_buffered_then_applied_in_order(tmp_path: Path):
    store = channel(tmp_path)
    publish(store, 3)
    assert store.receive_feedback("F2", "T1", 2, 2.0, 1) == "buffered_out_of_order"
    store.receive_feedback("F1", "T1", 1, 2.0, 2)
    store.receive_feedback("F3", "T1", 3, 2.0, 3)
    assert store.table("applied_feedback")["sequence"].tolist() == [1, 2, 3]
    store.close()


def test_timeout_advances_gap_and_rejects_late_feedback(tmp_path: Path):
    store = channel(tmp_path)
    publish(store, 3)
    store.receive_feedback("F2", "T1", 2, 2.0, 4)
    store.receive_feedback("F3", "T1", 3, 2.0, 4)
    assert store.expire_timeouts(6) == 1
    assert store.table("applied_feedback")["sequence"].tolist() == [2, 3]
    assert store.receive_feedback("F1", "T1", 1, 2.0, 7) == "late_rejected"
    assert 1 not in store.table("applied_feedback")["sequence"].tolist()
    store.close()


def test_restart_preserves_pending_and_buffered_state(tmp_path: Path):
    path = tmp_path / "channel.sqlite"
    store = ReliableCommandChannel(path, reset=True)
    store.register_task("T1", "algorithm", "parameters")
    publish(store, 3)
    store.receive_feedback("F2", "T1", 2, 2.0, 1)
    store.close()
    reopened = ReliableCommandChannel(path)
    assert reopened.recover(2) == {"tasks": 1, "pending_commands": 3, "buffered_feedback": 1}
    reopened.receive_feedback("F1", "T1", 1, 2.0, 2)
    assert reopened.table("applied_feedback")["sequence"].tolist() == [1, 2]
    reopened.close()


def test_command_idempotency_rejects_content_change(tmp_path: Path):
    store = channel(tmp_path)
    assert store.publish_command("T1", 1, "C1", 2.0, 0, 5)
    assert not store.publish_command("T1", 1, "C1", 2.0, 0, 5)
    with pytest.raises(ValueError, match="different content"):
        store.publish_command("T1", 1, "C1", 4.0, 0, 5)
    store.close()
