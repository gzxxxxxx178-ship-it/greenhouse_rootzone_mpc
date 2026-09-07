from pathlib import Path

from rootzone_mpc.experiments.confirmation import run_baseline_confirmation


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for path in run_baseline_confirmation(root):
        print(path)
