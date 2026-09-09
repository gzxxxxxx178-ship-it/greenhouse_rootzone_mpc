import argparse
from pathlib import Path

from rootzone_mpc.experiments.two_layer_identification import run_two_layer_identification


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Fit and validate the two-layer greybox model")
    parser.add_argument(
        "--config", type=Path,
        default=root / "configs/two_layer_identification_v1.yaml",
    )
    args = parser.parse_args()
    for output in run_two_layer_identification(root, args.config):
        print(output)
