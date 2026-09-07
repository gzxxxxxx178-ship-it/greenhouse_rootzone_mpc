import json
from pathlib import Path

from rootzone_mpc.platform.mqtt_bridge import (
    MqttFeedbackBridge,
    MqttFeedbackEnvelope,
)
from rootzone_mpc.platform.reliable_channel import ReliableCommandChannel


def setup(tmp_path: Path):
    channel = ReliableCommandChannel(tmp_path / "mqtt.sqlite", reset=True)
    channel.register_task("T1", "algorithm", "parameters")
    channel.publish_command("T1", 1, "C1", 2.0, 0, 100)
    return channel, MqttFeedbackBridge(channel, max_abs_clock_skew_seconds=5)


def test_envelope_round_trip_is_canonical():
    envelope = MqttFeedbackEnvelope(1, "F1", "T1", 1, 2.0, 1_000)
    assert MqttFeedbackEnvelope.from_payload(envelope.to_payload()) == envelope


def test_bridge_rejects_invalid_schema_without_touching_channel(tmp_path: Path):
    channel, bridge = setup(tmp_path)
    result = bridge.handle(json.dumps({"message_id": "F1"}).encode(), received_epoch_ns=1_000, received_tick=1)
    assert result.disposition == "invalid_schema"
    assert channel.scalar("SELECT COUNT(*) FROM feedback_inbox") == 0
    channel.close()


def test_bridge_rejects_clock_skew_before_application(tmp_path: Path):
    channel, bridge = setup(tmp_path)
    payload = MqttFeedbackEnvelope(1, "F1", "T1", 1, 2.0, 0).to_payload()
    result = bridge.handle(payload, received_epoch_ns=6_000_000_000, received_tick=1)
    assert result.disposition == "clock_skew_rejected"
    assert channel.scalar("SELECT COUNT(*) FROM applied_feedback") == 0
    channel.close()


def test_bridge_applies_valid_feedback(tmp_path: Path):
    channel, bridge = setup(tmp_path)
    payload = MqttFeedbackEnvelope(1, "F1", "T1", 1, 2.0, 1_000_000_000).to_payload()
    result = bridge.handle(payload, received_epoch_ns=1_001_000_000, received_tick=1)
    assert result.disposition == "ready"
    assert result.transport_latency_ms == 1.0
    assert channel.scalar("SELECT COUNT(*) FROM applied_feedback") == 1
    channel.close()
