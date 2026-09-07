from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import logging
import multiprocessing
import queue
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

import numpy as np
import pandas as pd
import paho.mqtt.client as mqtt
import yaml
from amqtt.broker import Broker

from rootzone_mpc.platform.mqtt_bridge import MqttFeedbackBridge, MqttFeedbackEnvelope
from rootzone_mpc.platform.reliable_channel import ReliableCommandChannel


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _broker_worker(port: int, ready: multiprocessing.synchronize.Event) -> None:
    logging.disable(logging.CRITICAL)

    async def serve() -> None:
        config = {
            "listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
            "plugins": {
                "amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": False}
            },
            "sys_interval": 0,
        }
        broker = Broker(config)
        await broker.start()
        ready.set()
        await asyncio.Event().wait()

    asyncio.run(serve())


class LocalBroker:
    def __init__(self, port: int):
        self.port = port
        self.context = multiprocessing.get_context("spawn")
        self.process = None

    def start(self, timeout: float) -> None:
        ready = self.context.Event()
        self.process = self.context.Process(target=_broker_worker, args=(self.port, ready))
        self.process.start()
        if not ready.wait(timeout) or not self.process.is_alive():
            raise RuntimeError("Embedded MQTT broker did not start")

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=5)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=5)
        self.process = None


@dataclass
class ClientState:
    connected: Event = field(default_factory=Event)
    subscribed: Event = field(default_factory=Event)
    messages: queue.Queue = field(default_factory=queue.Queue)
    session_present: list[bool] = field(default_factory=list)


def _subscriber(client_id: str, topic: str, state: ClientState) -> mqtt.Client:
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        clean_session=False,
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set("rootzone-sil")

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            return
        session_present = bool(flags.session_present)
        state.session_present.append(session_present)
        state.connected.set()
        if session_present:
            state.subscribed.set()
        else:
            state.subscribed.clear()
            client.subscribe(topic, qos=1)

    def on_subscribe(client, userdata, mid, reason_code_list, properties):
        state.subscribed.set()

    def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
        state.connected.clear()
        state.subscribed.clear()

    def on_message(client, userdata, message):
        state.messages.put({
            "payload": bytes(message.payload),
            "qos": int(message.qos),
            "dup": bool(message.dup),
            "received_epoch_ns": time.time_ns(),
            "received_monotonic_ns": time.monotonic_ns(),
        })

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=1)
    return client


def _publisher(client_id: str, state: ClientState) -> mqtt.Client:
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        clean_session=True,
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set("rootzone-sil")

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            return
        state.connected.set()

    def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
        state.connected.clear()

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.reconnect_delay_set(min_delay=1, max_delay=1)
    return client


def _connect(client: mqtt.Client, state: ClientState, port: int, timeout: float) -> None:
    client.connect("127.0.0.1", port, keepalive=5)
    client.loop_start()
    if not state.connected.wait(timeout):
        raise TimeoutError("MQTT client connection timed out")


def _stop_client(client: mqtt.Client) -> None:
    try:
        client.disconnect()
    finally:
        client.loop_stop()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_disconnect(state: ClientState, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while state.connected.is_set() and time.monotonic() < deadline:
        time.sleep(0.02)
    return not state.connected.is_set()


def run_mqtt_sil(project_root: Path) -> tuple[Path, ...]:
    cfg = yaml.safe_load((project_root / "configs/mqtt_sil_v1.yaml").read_text())["mqtt_sil"]
    timeout = float(cfg["client_timeout_seconds"])
    topic = str(cfg["topic"])
    qos = int(cfg["qos"])
    port = _free_port()
    broker = LocalBroker(port)
    database_path = project_root / "outputs/runs/mqtt_sil_v1.sqlite"
    channel = ReliableCommandChannel(database_path, reset=True)
    bridge = MqttFeedbackBridge(channel, float(cfg["max_abs_clock_skew_seconds"]))
    start_monotonic_ns = time.monotonic_ns()
    receipts: list[dict] = []
    subscriber = publisher = None

    controller_sha = _sha(project_root / "configs/frozen_controller_v1.yaml")
    supervision_sha = _sha(project_root / "configs/frozen_supervision_v1.yaml")

    def publish(envelope: MqttFeedbackEnvelope) -> None:
        info = publisher.publish(topic, envelope.to_payload(), qos=qos)
        info.wait_for_publish(timeout=timeout)
        if not info.is_published():
            raise TimeoutError(f"MQTT publish not acknowledged: {envelope.message_id}")

    def consume(state: ClientState, expected_label: str) -> dict:
        item = state.messages.get(timeout=timeout)
        received_tick = int((item["received_monotonic_ns"] - start_monotonic_ns) / 1_000_000)
        outcome = bridge.handle(
            item["payload"], received_epoch_ns=item["received_epoch_ns"],
            received_tick=received_tick,
        )
        record = {
            "label": expected_label,
            "message_id": outcome.message_id,
            "task_id": outcome.task_id,
            "sequence": outcome.sequence,
            "qos": item["qos"],
            "mqtt_dup_flag": item["dup"],
            "disposition": outcome.disposition,
            "transport_latency_ms": outcome.transport_latency_ms,
        }
        receipts.append(record)
        return record

    try:
        broker.start(timeout)
        subscriber_state = ClientState()
        publisher_state = ClientState()
        subscriber = _subscriber("rootzone-feedback-consumer", topic, subscriber_state)
        publisher = _publisher("rootzone-feedback-producer", publisher_state)
        _connect(subscriber, subscriber_state, port, timeout)
        if not subscriber_state.subscribed.wait(timeout):
            raise TimeoutError("MQTT subscription timed out")
        _connect(publisher, publisher_state, port, timeout)

        online_count = int(cfg["online_message_count"])
        channel.register_task("MQTT-MAIN", controller_sha, supervision_sha)
        for sequence in range(1, online_count + 3):
            channel.publish_command(
                "MQTT-MAIN", sequence, f"MQTT-CMD-{sequence:03d}",
                float(sequence % 2) * 2.0, issued_tick=0, timeout_ticks=120_000,
            )
        for sequence in range(1, online_count + 1):
            envelope = MqttFeedbackEnvelope(
                1, f"MQTT-FB-{sequence:03d}", "MQTT-MAIN", sequence,
                float(sequence % 2) * 2.0, time.time_ns(),
            )
            publish(envelope)
            consume(subscriber_state, "online")

        duplicate = MqttFeedbackEnvelope(
            1, "MQTT-FB-DUP", "MQTT-MAIN", online_count + 1,
            float((online_count + 1) % 2) * 2.0, time.time_ns(),
        )
        publish(duplicate)
        publish(duplicate)
        first_duplicate = consume(subscriber_state, "duplicate_first")
        second_duplicate = consume(subscriber_state, "duplicate_repeat")

        _stop_client(subscriber)
        subscriber = None
        offline = MqttFeedbackEnvelope(
            1, "MQTT-FB-OFFLINE", "MQTT-MAIN", online_count + 2,
            float((online_count + 2) % 2) * 2.0, time.time_ns(),
        )
        publish(offline)
        resumed_state = ClientState()
        subscriber = _subscriber("rootzone-feedback-consumer", topic, resumed_state)
        _connect(subscriber, resumed_state, port, timeout)
        if not resumed_state.subscribed.wait(timeout):
            raise TimeoutError("Persistent MQTT session did not resume")
        offline_receipt = consume(resumed_state, "offline_replay")
        offline_session_present = resumed_state.session_present[-1]
        channel.complete_task("MQTT-MAIN", int((time.monotonic_ns() - start_monotonic_ns) / 1_000_000))

        channel.register_task("MQTT-RESTART", controller_sha, supervision_sha)
        channel.publish_command("MQTT-RESTART", 1, "MQTT-RESTART-CMD", 2.0, 0, 120_000)
        broker.stop()
        subscriber_disconnected = _wait_for_disconnect(resumed_state, timeout)
        publisher_disconnected = _wait_for_disconnect(publisher_state, timeout)
        broker.start(timeout)
        if not resumed_state.connected.wait(timeout) or not resumed_state.subscribed.wait(timeout):
            raise TimeoutError("Subscriber did not reconnect and resubscribe after broker restart")
        if not publisher_state.connected.wait(timeout):
            raise TimeoutError("Publisher did not reconnect after broker restart")
        restart_session_present = resumed_state.session_present[-1]
        restart_envelope = MqttFeedbackEnvelope(
            1, "MQTT-FB-RESTART", "MQTT-RESTART", 1, 2.0, time.time_ns()
        )
        publish(restart_envelope)
        restart_receipt = consume(resumed_state, "broker_restart")
        channel.complete_task("MQTT-RESTART", int((time.monotonic_ns() - start_monotonic_ns) / 1_000_000))

        channel.register_task("MQTT-CLOCK", controller_sha, supervision_sha)
        channel.publish_command("MQTT-CLOCK", 1, "MQTT-CLOCK-CMD-1", 0.0, 0, 120_000)
        channel.publish_command("MQTT-CLOCK", 2, "MQTT-CLOCK-CMD-2", 0.0, 0, 120_000)
        now = time.time_ns()
        publish(MqttFeedbackEnvelope(1, "MQTT-FB-STALE", "MQTT-CLOCK", 1, 0.0, now - 60_000_000_000))
        publish(MqttFeedbackEnvelope(1, "MQTT-FB-FUTURE", "MQTT-CLOCK", 2, 0.0, now + 60_000_000_000))
        stale_receipt = consume(resumed_state, "stale_clock")
        future_receipt = consume(resumed_state, "future_clock")
        channel.expire_timeouts(120_001)
        channel.complete_task("MQTT-CLOCK", 120_002)

        receipts_frame = pd.DataFrame(receipts)
        online_latency = receipts_frame.loc[
            receipts_frame["label"] == "online", "transport_latency_ms"
        ].astype(float)
        applied = channel.table("applied_feedback")
        main_applied = applied[applied["task_id"] == "MQTT-MAIN"]
        invariants = {
            "all_online_messages_received": len(online_latency) == online_count,
            "qos1_observed_end_to_end": bool((receipts_frame["qos"] == qos).all()),
            "application_duplicate_suppressed": first_duplicate["disposition"] == "ready"
            and second_duplicate["disposition"] == "duplicate_message"
            and len(main_applied[main_applied["sequence"] == online_count + 1]) == 1,
            "persistent_session_replays_offline_message": offline_session_present
            and offline_receipt["disposition"] == "ready",
            "broker_restart_reconnects_and_resubscribes": subscriber_disconnected
            and publisher_disconnected and not restart_session_present
            and restart_receipt["disposition"] == "ready",
            "stale_and_future_timestamps_rejected": stale_receipt["disposition"] == "clock_skew_rejected"
            and future_receipt["disposition"] == "clock_skew_rejected",
            "online_messages_meet_deadline": bool(
                float(online_latency.max()) <= float(cfg["end_to_end_deadline_ms"])
            ),
            "mqtt_feedback_matches_audit_store": len(applied) == online_count + 3,
        }
        if set(invariants) != set(cfg["required_invariants"]) or not all(invariants.values()):
            raise AssertionError(f"MQTT SIL invariant failed: {invariants}")

        table_path = project_root / "outputs/tables/mqtt_sil_receipts_v1.csv"
        table_path.parent.mkdir(parents=True, exist_ok=True)
        receipts_frame.to_csv(table_path, index=False)
        result = {
            "name": cfg["name"],
            "status": "passed",
            "protocol": cfg["protocol"],
            "qos": qos,
            "online_message_count": online_count,
            "total_received_message_count": int(len(receipts_frame)),
            "applied_feedback_count": int(len(applied)),
            "online_latency_ms": {
                "median": float(np.median(online_latency)),
                "p95": float(np.percentile(online_latency, 95)),
                "maximum": float(online_latency.max()),
                "deadline": float(cfg["end_to_end_deadline_ms"]),
            },
            "offline_session_present": offline_session_present,
            "restart_session_present": restart_session_present,
            "invariants": invariants,
            "library_versions": {
                "amqtt": importlib.metadata.version("amqtt"),
                "paho_mqtt": importlib.metadata.version("paho-mqtt"),
            },
            "version_hashes": {"controller": controller_sha, "supervision": supervision_sha},
            "evidence_boundary": cfg["evidence_boundary"],
            "reproducibility_note": "Latency is a runtime measurement and is not deterministic.",
        }
        result_path = project_root / "data/processed/mqtt_sil_v1.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        if subscriber is not None:
            _stop_client(subscriber)
        if publisher is not None:
            _stop_client(publisher)
        broker.stop()
        channel.close()

    return database_path, table_path, result_path
