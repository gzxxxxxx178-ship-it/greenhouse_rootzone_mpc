from __future__ import annotations

import argparse
import json
from pathlib import Path

from rootzone_mpc.data.calibration_quality import run_calibration_assessment


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Assess soil-moisture and flow calibration data")
    parser.add_argument("moisture", type=Path)
    parser.add_argument("flow", type=Path)
    parser.add_argument(
        "--config", type=Path, default=root / "configs/field_calibration_quality_v1.yaml"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_calibration_assessment(
        args.moisture, args.flow, args.config, root, args.output
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "passed" else 2)
