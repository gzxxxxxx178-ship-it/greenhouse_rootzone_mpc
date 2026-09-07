from pathlib import Path

from rootzone_mpc.experiments.mqtt_sil import run_mqtt_sil


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for output in run_mqtt_sil(root):
        print(output)
