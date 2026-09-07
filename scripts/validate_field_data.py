from __future__ import annotations

import argparse
import json
from pathlib import Path

from rootzone_mpc.data.field_quality import validate_csv


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Validate root-zone field observations")
    parser.add_argument("input", type=Path)
    parser.add_argument("--config", type=Path, default=root / "configs/field_data_quality_v1.yaml")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate_csv(args.input, args.config, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "passed" else 2)
