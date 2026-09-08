from __future__ import annotations

import argparse
import json
from pathlib import Path

from rootzone_mpc.experiments.two_layer_bridge_data import write_bridge_identification_data


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Generate independent two-layer bridge data")
    parser.add_argument(
        "--config", type=Path,
        default=root / "configs/two_layer_closed_loop_protocol_v1.yaml",
    )
    parser.add_argument(
        "--output", type=Path,
        default=root / "data/examples/two_layer_bridge_observations_v1.csv",
    )
    args = parser.parse_args()
    _, diagnostics = write_bridge_identification_data(args.config, args.output)
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))
