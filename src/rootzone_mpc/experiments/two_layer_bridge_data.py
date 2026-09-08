from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rootzone_mpc.models.two_layer_plant import (
    TwoLayerPlantParameters,
    TwoLayerRootZonePlant,
)


def load_protocol(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["two_layer_closed_loop"]


def _humidity_for_vpd(temperature_c: float, vpd_kpa: float) -> float:
    saturation = 0.6108 * np.exp(17.27 * temperature_c / (temperature_c + 237.3))
    return float(np.clip(100.0 * (1.0 - vpd_kpa / saturation), 20.0, 95.0))


def _weather(step: int, cycle_index: int) -> tuple[float, float, float, float]:
    hour = (step * 0.25) % 24.0
    daylight = max(np.sin(np.pi * (hour - 6.0) / 12.0), 0.0)
    solar_peak = 520.0 + 45.0 * ((cycle_index % 3) - 1)
    cloud_wave = 0.82 + 0.18 * np.sin(
        2.0 * np.pi * step / 31.0 + 0.7 * cycle_index
    )
    solar = solar_peak * daylight * cloud_wave
    temperature = 21.0 + 7.0 * daylight + 0.6 * ((cycle_index % 4) - 1.5)
    phase = 0.85 * cycle_index
    vpd = np.clip(
        0.72 + 0.25 * daylight
        + 0.38 * np.sin(2.0 * np.pi * hour / 24.0 + phase),
        0.15,
        1.80,
    )
    humidity = _humidity_for_vpd(temperature, vpd)
    return float(solar), float(vpd), float(temperature), humidity


def _pulse_steps(cycle_index: int, steps: int) -> set[int]:
    start = 2 + (cycle_index * 4) % 13
    count = 10 + 2 * (cycle_index % 3)
    pulses = {start + offset for offset in range(count)}
    return {step for step in pulses if 1 <= step <= steps}


def _initial_state(cycle_index: int) -> tuple[float, float]:
    states = [
        (0.190, 0.205), (0.205, 0.190), (0.215, 0.215),
        (0.185, 0.220), (0.225, 0.195), (0.198, 0.212),
        (0.192, 0.218), (0.220, 0.198), (0.202, 0.208),
    ]
    return states[cycle_index]


def generate_bridge_identification_data(protocol: dict) -> tuple[pd.DataFrame, dict]:
    time_cfg = protocol["time"]
    design = protocol["data_design"]
    noise = protocol["synthetic_noise_reference"]
    plant_values = dict(protocol["plant_nominal"])
    plant_values.update({
        "process_noise_sd_m3_m3": float(noise["process_noise_sd_m3_m3"]),
        "measurement_noise_sd_m3_m3": float(noise["measurement_noise_sd_m3_m3"]),
        "command_to_delivered_relative_sd": float(noise["command_to_delivered_relative_sd"]),
    })
    parameters = TwoLayerPlantParameters(**plant_values)
    step_minutes = int(time_cfg["step_minutes"])
    steps = int(time_cfg["identification_steps_per_cycle"])
    role_counts = {
        "identification": int(design["identification_cycles"]),
        "validation": int(design["validation_cycles"]),
    }
    start_time = datetime(2026, 5, 1, tzinfo=timezone.utc)
    zone_area_m2 = 10.0
    rows: list[dict] = []
    maximum_balance_residual = 0.0
    cycle_index = 0
    for role, count in role_counts.items():
        base_seed = int(design[f"{role}_seed"])
        for local_index in range(count):
            shallow, deep = _initial_state(cycle_index)
            plant = TwoLayerRootZonePlant(parameters, shallow, deep, base_seed + local_index)
            measured_shallow, measured_deep = plant.measure()
            pulses = _pulse_steps(cycle_index, steps)
            cumulative_l = 0.0
            cycle_start = start_time + timedelta(days=2 * cycle_index)
            for step in range(steps + 1):
                solar, vpd, temperature, humidity = _weather(step, cycle_index)
                command = 0.0 if step == 0 else (1.0 if step in pulses else 0.0)
                delivered = 0.0
                flow = 0.0
                if step > 0:
                    outcome = plant.step(command, solar, vpd)
                    measured_shallow = outcome.theta_shallow_measured
                    measured_deep = outcome.theta_deep_measured
                    delivered = outcome.irrigation_delivered_mm
                    cumulative_l += delivered * zone_area_m2
                    flow = delivered * zone_area_m2 / step_minutes
                    maximum_balance_residual = max(
                        maximum_balance_residual, abs(outcome.water_balance_residual_mm)
                    )
                reference = step in {0, steps}
                timestamp = cycle_start + timedelta(minutes=step * step_minutes)
                rows.append({
                    "timestamp_utc": timestamp.isoformat().replace("+00:00", "Z"),
                    "site_id": "SYNTHETIC_TWO_LAYER_SITE",
                    "zone_id": "ZONE_01",
                    "cycle_id": f"B{cycle_index + 1:02d}",
                    "dataset_role": role,
                    "sample_interval_minutes": step_minutes,
                    "zone_area_m2": zone_area_m2,
                    "theta_10cm_m3_m3": measured_shallow,
                    "theta_25cm_m3_m3": measured_deep,
                    "irrigation_command_mm": command,
                    "irrigation_delivered_mm": delivered,
                    "flow_l_min": flow,
                    "cumulative_water_l": cumulative_l,
                    "air_temperature_c": temperature,
                    "relative_humidity_pct": humidity,
                    "solar_radiation_w_m2": solar,
                    "reference_theta_10cm_m3_m3": plant.theta_shallow if reference else np.nan,
                    "reference_theta_25cm_m3_m3": plant.theta_deep if reference else np.nan,
                    "root_depth_m": (
                        parameters.shallow_depth_mm + parameters.deep_depth_mm
                    ) / 1000.0,
                    "crop_stage": "synthetic_vegetative",
                    "sensor_status": "ok",
                    "notes": "independent_synthetic_two_layer_bridge_only",
                })
            cycle_index += 1
    frame = pd.DataFrame(rows)
    diagnostics = {
        "row_count": int(len(frame)),
        "cycle_counts": role_counts,
        "steps_per_cycle": steps,
        "maximum_absolute_water_balance_residual_mm": float(maximum_balance_residual),
        "plant_parameter_sha256": hashlib.sha256(
            repr(parameters).encode("utf-8")
        ).hexdigest(),
        "plant_imports_fitted_model_parameters": False,
    }
    return frame, diagnostics


def write_bridge_identification_data(
    protocol_path: Path, output_path: Path
) -> tuple[Path, dict]:
    protocol = load_protocol(protocol_path)
    frame, diagnostics = generate_bridge_identification_data(protocol)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, index=False)
    diagnostics["output_sha256"] = hashlib.sha256(output_path.read_bytes()).hexdigest()
    return output_path, diagnostics
