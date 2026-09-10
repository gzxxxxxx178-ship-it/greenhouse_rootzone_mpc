from pathlib import Path

from rootzone_mpc.experiments.two_layer_confirmation import run_two_layer_confirmation


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for output in run_two_layer_confirmation(root):
        print(output)
