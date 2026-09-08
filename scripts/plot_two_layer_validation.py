from __future__ import annotations

import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica", "sans-serif"],
    "font.size": 7,
    "axes.linewidth": 0.7,
    "axes.spines.right": False,
    "axes.spines.top": False,
    "legend.frameon": False,
    "pdf.fonttype": 42,
    "svg.fonttype": "none",
})

COLORS = {"shallow": "#2E6F9E", "deep": "#D17A22", "neutral": "#5B6573"}


def make_figure(project_root: Path) -> tuple[Path, ...]:
    predictions = pd.read_csv(
        project_root / "outputs/tables/two_layer_validation_predictions_v1.csv"
    )
    result = json.loads(
        (project_root / "data/processed/two_layer_identification_smoke_v1.json").read_text()
    )
    cycles = sorted(predictions["cycle_id"].unique())
    if len(cycles) != 2:
        raise ValueError("The frozen smoke figure requires exactly two validation cycles")

    fig, axes = plt.subplots(2, 2, figsize=(7.1, 5.0), constrained_layout=True)
    for panel, (axis, cycle_id) in enumerate(zip(axes[0], cycles, strict=True)):
        cycle = predictions[predictions["cycle_id"] == cycle_id].reset_index(drop=True)
        elapsed_hours = np.arange(1, len(cycle) + 1) * 0.25
        for depth, label in [("shallow", "10 cm"), ("deep", "25 cm")]:
            color = COLORS[depth]
            axis.plot(
                elapsed_hours, cycle[f"observed_theta_{depth}"], color=color,
                linewidth=1.5, label=f"Observed {label}",
            )
            axis.plot(
                elapsed_hours, cycle[f"predicted_theta_{depth}"], color=color,
                linewidth=1.2, linestyle="--", label=f"Model {label}",
            )
        axis.set_title(f"Validation cycle {cycle_id}", loc="left", fontweight="bold")
        axis.set_xlabel("Elapsed time (h)")
        axis.set_ylabel("Volumetric water content (m³ m⁻³)", fontsize=8)
        axis.grid(axis="y", color="#D9DEE5", linewidth=0.5)
        axis.text(-0.13, 1.05, chr(ord("a") + panel), transform=axis.transAxes,
                  fontsize=9, fontweight="bold")
    axes[0, 0].legend(ncol=2, loc="upper right", fontsize=6.3, handlelength=2.5)

    parity = axes[1, 0]
    all_values = []
    for depth, label in [("shallow", "10 cm"), ("deep", "25 cm")]:
        observed = predictions[f"observed_theta_{depth}"].to_numpy()
        predicted = predictions[f"predicted_theta_{depth}"].to_numpy()
        all_values.extend(observed.tolist() + predicted.tolist())
        parity.scatter(
            observed, predicted, s=12, alpha=0.72, color=COLORS[depth],
            edgecolors="white", linewidths=0.25, label=label,
        )
    lower, upper = min(all_values), max(all_values)
    margin = (upper - lower) * 0.08
    parity.plot([lower - margin, upper + margin], [lower - margin, upper + margin],
                color=COLORS["neutral"], linewidth=0.8, linestyle=":")
    parity.set_xlim(lower - margin, upper + margin)
    parity.set_ylim(lower - margin, upper + margin)
    parity.set_aspect("equal", adjustable="box")
    parity.set_xlabel("Observed water content (m³ m⁻³)", fontsize=8)
    parity.set_ylabel("Rollout prediction (m³ m⁻³)", fontsize=8)
    parity.set_title("Whole-cycle prediction agreement", loc="left", fontweight="bold")
    parity.legend(loc="lower right")
    parity.text(-0.13, 1.05, "c", transform=parity.transAxes,
                fontsize=9, fontweight="bold")

    skill_axis = axes[1, 1]
    labels = ["10 cm", "25 cm"]
    one_step = [result["one_step_validation"][key]["skill_vs_baseline"]
                for key in ["theta_10cm", "theta_25cm"]]
    rollout = [result["rollout_validation"][key]["skill_vs_baseline"]
               for key in ["theta_10cm", "theta_25cm"]]
    positions = np.arange(len(labels))
    width = 0.34
    bars_one = skill_axis.bar(
        positions - width / 2, one_step, width, color="#9ABBD3", label="One-step",
    )
    bars_rollout = skill_axis.bar(
        positions + width / 2, rollout, width, color="#2E6F9E", label="Whole-cycle",
    )
    skill_axis.axhline(0, color=COLORS["neutral"], linewidth=0.7)
    skill_axis.set_xticks(positions, labels)
    skill_axis.set_ylim(0, 1.02)
    skill_axis.set_ylabel("Skill versus frozen baseline")
    skill_axis.set_title("Validation skill", loc="left", fontweight="bold")
    skill_axis.grid(axis="y", color="#D9DEE5", linewidth=0.5)
    skill_axis.legend(loc="upper right")
    skill_axis.bar_label(bars_one, fmt="%.2f", padding=2, fontsize=6.3)
    skill_axis.bar_label(bars_rollout, fmt="%.2f", padding=2, fontsize=6.3)
    skill_axis.text(
        0.02, 0.04, f"n = {result['validation_transition_count']} transitions",
        transform=skill_axis.transAxes, color=COLORS["neutral"], fontsize=6.3,
    )
    skill_axis.text(-0.13, 1.05, "d", transform=skill_axis.transAxes,
                    fontsize=9, fontweight="bold")

    fig.suptitle(
        "Synthetic two-layer root-zone model validation — pipeline smoke test",
        fontsize=10, fontweight="bold",
    )
    output_base = project_root / "outputs/figures/two_layer_validation_smoke_v1"
    output_base.parent.mkdir(parents=True, exist_ok=True)
    svg_path = output_base.with_suffix(".svg")
    pdf_path = output_base.with_suffix(".pdf")
    tiff_path = output_base.with_suffix(".tiff")
    png_path = output_base.with_suffix(".png")
    fig.savefig(svg_path, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    fig.savefig(tiff_path, dpi=600, bbox_inches="tight")
    fig.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return svg_path, pdf_path, tiff_path, png_path


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for output in make_figure(root):
        print(output)
