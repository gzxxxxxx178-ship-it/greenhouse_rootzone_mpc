from __future__ import annotations

from pathlib import Path

import pandas as pd


def moisture_example() -> pd.DataFrame:
    rows = []
    reference_levels = [0.12, 0.18, 0.24, 0.30, 0.36]
    sensor_models = {
        ("S10", "10cm"): (0.004, 0.97),
        ("S25", "25cm"): (-0.003, 1.04),
    }
    replicate_noise = [-0.003, 0.0, 0.003]
    water_density = 0.998
    for sensor_index, ((sensor_id, depth), (intercept, slope)) in enumerate(
        sensor_models.items()
    ):
        for level_index, reference_theta in enumerate(reference_levels, start=1):
            for replicate, noise in enumerate(replicate_noise, start=1):
                measured = (reference_theta - intercept) / slope + noise
                dry_mass = 120.0 + sensor_index
                wet_mass = dry_mass + reference_theta * 100.0 * water_density
                rows.append({
                    "timestamp_utc": f"2026-03-{level_index:02d}T0{replicate}:00:00Z",
                    "calibration_batch_id": "SYNTHETIC_MOISTURE_V1",
                    "soil_source_id": "SYNTHETIC_SOIL",
                    "sensor_id": sensor_id,
                    "depth_label": depth,
                    "moisture_level_id": f"L{level_index:02d}",
                    "replicate_id": f"R{replicate:02d}",
                    "sensor_theta_m3_m3": round(measured, 6),
                    "wet_soil_mass_g": round(wet_mass, 6),
                    "dry_soil_mass_g": round(dry_mass, 6),
                    "sample_volume_cm3": 100.0,
                    "water_density_g_cm3": water_density,
                    "reference_method": "synthetic_oven_dry_equivalent",
                    "operator_id": "SYNTHETIC",
                    "notes": "pipeline_example_only",
                })
    return pd.DataFrame(rows)


def flow_example() -> pd.DataFrame:
    rows = []
    reference_volumes = [10.0, 20.0, 30.0]
    device_noise = [-0.10, 0.0, 0.10]
    density = 0.998
    for point_index, reference_volume in enumerate(reference_volumes, start=1):
        for replicate, noise in enumerate(device_noise, start=1):
            reported = (reference_volume - 0.05) / 0.98 + noise
            initial_mass = 5.0
            final_mass = initial_mass + reference_volume * density
            rows.append({
                "timestamp_utc": f"2026-03-{10 + point_index:02d}T0{replicate}:00:00Z",
                "calibration_batch_id": "SYNTHETIC_FLOW_V1",
                "device_id": "FLOW_01",
                "operating_point_id": f"Q{point_index:02d}",
                "replicate_id": f"R{replicate:02d}",
                "duration_s": 600.0,
                "initial_container_mass_kg": initial_mass,
                "final_container_mass_kg": round(final_mass, 6),
                "water_density_kg_l": density,
                "device_reported_volume_l": round(reported, 6),
                "reference_method": "synthetic_gravimetric_equivalent",
                "operator_id": "SYNTHETIC",
                "notes": "pipeline_example_only",
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    moisture_path = root / "data/examples/synthetic_moisture_calibration_v1.csv"
    flow_path = root / "data/examples/synthetic_flow_calibration_v1.csv"
    moisture_example().to_csv(moisture_path, index=False)
    flow_example().to_csv(flow_path, index=False)
    print(moisture_path)
    print(flow_path)
