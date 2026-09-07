from __future__ import annotations

import math
from datetime import timedelta
from pathlib import Path

import pandas as pd


def build_example() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-04-01T00:00:00Z")
    for cycle_number in range(1, 6):
        role = "identification" if cycle_number <= 3 else "validation"
        cycle_id = f"C{cycle_number:02d}"
        cumulative_l = 0.0
        for step in range(48):
            delivered_mm = 2.0 if step == 1 else 0.0
            flow_l_min = 20.0 / 15.0 if step == 1 else 0.0
            cumulative_l += delivered_mm * 10.0
            if step == 0:
                shallow = 0.215 + 0.001 * cycle_number
                deep = 0.225 + 0.0005 * cycle_number
            else:
                shallow = 0.218 + 0.028 * math.exp(-(step - 1) / 15)
                deep = 0.224 + 0.014 * math.exp(-(step - 1) / 24)
            timestamp = start + timedelta(days=cycle_number - 1, minutes=15 * step)
            reference = step in {0, 1, 47}
            solar = max(0.0, 550 * math.sin(math.pi * (step % 48) / 48))
            rows.append({
                "timestamp_utc": timestamp.isoformat().replace("+00:00", "Z"),
                "site_id": "SYNTHETIC_SITE",
                "zone_id": "ZONE_01",
                "cycle_id": cycle_id,
                "dataset_role": role,
                "sample_interval_minutes": 15,
                "zone_area_m2": 10.0,
                "theta_10cm_m3_m3": round(shallow, 6),
                "theta_25cm_m3_m3": round(deep, 6),
                "irrigation_command_mm": delivered_mm,
                "irrigation_delivered_mm": delivered_mm,
                "flow_l_min": round(flow_l_min, 6),
                "cumulative_water_l": cumulative_l,
                "air_temperature_c": round(22 + 5 * math.sin(2 * math.pi * step / 48), 3),
                "relative_humidity_pct": round(70 - 12 * math.sin(2 * math.pi * step / 48), 3),
                "solar_radiation_w_m2": round(solar, 3),
                "reference_theta_10cm_m3_m3": round(shallow - 0.001, 6) if reference else "",
                "reference_theta_25cm_m3_m3": round(deep + 0.001, 6) if reference else "",
                "root_depth_m": 0.30,
                "crop_stage": "vegetative",
                "sensor_status": "ok",
                "notes": "synthetic_example_only",
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    output = root / "data/examples/synthetic_field_observations_v1.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    build_example().to_csv(output, index=False)
    print(output)
