from pathlib import Path

from rootzone_mpc.experiments.two_layer_identification import run_two_layer_identification


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for output in run_two_layer_identification(root):
        print(output)
