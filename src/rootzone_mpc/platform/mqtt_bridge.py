from __future__ import annotations

import json
import math
from dataclasses import dataclass

from rootzone_mpc.platform.reliable_channel import ReliableCommandChannel


@dataclass(frozen=True)
class MqttFeedbackEnvelope:
    schema_version: int
    message_id: str
    task_id: str
    sequence: int
    delivered_mm: float
    source_timestamp_ns: int

    @classmethod
    def from_payload(cls, payload: bytes) -> "MqttFeedbackEnvelope":
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_json") from exc
        required = {
            "schema_version", "message_id", "task_id", "sequence",
            "delivered_mm", "source_timestamp_ns",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError("invalid_schema")
        if value["schema_version"] != 1:
            raise ValueError("unsupported_schema_version")
        if not isinstance(value["message_id"], str) or not value["message_id"]:
            raise ValueError("invalid_message_id")
        if not isinstance(value["task_id"], str) or not value["task_id"]:
            raise ValueError("invalid_task_id")
        if not isinstance(value["sequence"], int) or isinstance(value["sequence"], bool) or value["sequence"] < 1:
            raise ValueError("invalid_sequence")
        if not isinstance(value["delivered_mm"], (int, float)) or isinstance(value["delivered_mm"], bool):
            raise ValueError("invalid_delivered_mm")
        if not math.isfinite(float(value["delivered_mm"])) or float(value["delivered_mm"]) < 0:
            raise ValueError("invalid_delivered_mm")
        if not isinstance(value["source_timestamp_ns"], int) or isinstance(value["source_timestamp_ns"], bool):
            raise ValueError("invalid_source_timestamp")
        return cls(
            schema_version=1,
            message_id=value["message_id"],
            task_id=value["task_id"],
            sequence=value["sequence"],
            delivered_mm=float(value["delivered_mm"]),
            source_timestamp_ns=value["source_timestamp_ns"],
        )

    def to_payload(self) -> bytes:
        return json.dumps(self.__dict__, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class MqttBridgeResult:
    disposition: str
    message_id: str | None
    task_id: str | None
    sequence: int | None
    transport_latency_ms: float | None


class MqttFeedbackBridge:
    def __init__(self, channel: ReliableCommandChannel, max_abs_clock_skew_seconds: float):
        if max_abs_clock_skew_seconds <= 0:
            raise ValueError("max_abs_clock_skew_seconds must be positive")
        self.channel = channel
        self.max_abs_clock_skew_ns = int(max_abs_clock_skew_seconds * 1_000_000_000)

    def handle(
        self, payload: bytes, *, received_epoch_ns: int, received_tick: int
    ) -> MqttBridgeResult:
        try:
            envelope = MqttFeedbackEnvelope.from_payload(payload)
        except ValueError as exc:
            return MqttBridgeResult(str(exc), None, None, None, None)
        offset_ns = received_epoch_ns - envelope.source_timestamp_ns
        if abs(offset_ns) > self.max_abs_clock_skew_ns:
            return MqttBridgeResult(
                "clock_skew_rejected", envelope.message_id, envelope.task_id, envelope.sequence,
                offset_ns / 1_000_000,
            )
        disposition = self.channel.receive_feedback(
            envelope.message_id, envelope.task_id, envelope.sequence,
            envelope.delivered_mm, received_tick,
        )
        return MqttBridgeResult(
            disposition, envelope.message_id, envelope.task_id, envelope.sequence,
            offset_ns / 1_000_000,
        )
