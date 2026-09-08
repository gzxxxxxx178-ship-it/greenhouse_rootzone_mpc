from __future__ import annotations

import argparse
import json
from pathlib import Path

from rootzone_mpc.data.dynamic_noise import run_dynamic_noise_assessment


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Separate validation residual variance from calibrated measurement and flow error"
    )
    parser.add_argument(
        "--config", type=Path, default=root / "configs/dynamic_noise_decomposition_v1.yaml"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_dynamic_noise_assessment(root, args.config, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    raise SystemExit(0 if result["status"] == "passed" else 2)
