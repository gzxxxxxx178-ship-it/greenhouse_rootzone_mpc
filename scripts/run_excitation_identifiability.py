from pathlib import Path

from rootzone_mpc.experiments.excitation_identifiability import run_excitation_identifiability


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for output in run_excitation_identifiability(root):
        print(output)
