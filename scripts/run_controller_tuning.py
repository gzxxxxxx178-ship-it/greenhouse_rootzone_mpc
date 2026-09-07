from pathlib import Path

from rootzone_mpc.experiments.development import run_controller_tuning


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for path in run_controller_tuning(root):
        print(path)
