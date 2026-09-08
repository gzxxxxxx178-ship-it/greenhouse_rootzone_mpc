from pathlib import Path

from rootzone_mpc.experiments.information_identifiability import run_information_identifiability


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for output in run_information_identifiability(root):
        print(output)
