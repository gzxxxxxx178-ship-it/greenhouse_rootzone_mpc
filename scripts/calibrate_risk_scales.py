from pathlib import Path

from rootzone_mpc.experiments.risk_calibration import calibrate_risk_scales


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for path in calibrate_risk_scales(root):
        print(path)
